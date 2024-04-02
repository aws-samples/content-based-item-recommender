from constructs import Construct
from aws_cdk import (
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_apigatewayv2 as apigw2,
    aws_iam as iam,
    aws_ssm as ssm,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    aws_s3_deployment as s3deploy,
    Aws, Stack, NestedStack, Duration, BundlingOptions
)
from cdk_nag import NagSuppressions
import json

class APIStack(NestedStack):
    def __init__(self, 
                 scope: Construct, 
                 id: str, 
                 vpc, 
                 private_with_egress_subnets, 
                 bucket, 
                 db_writer_endpoint, 
                 db_reader_endpoint, 
                 database_name: str,
                 db_secret_arn: str,
                 environment,
                 **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        
        aws_region = Stack.of(self).region
        aws_account_id = Stack.of(self).account
        
        # ====== API COMMON ======

        # CloudWatch Logs
        ws_access_log_group = logs.LogGroup(self, "WSAccessLogGroup")
        rest_access_log_group = logs.LogGroup(self, "RestAPIAccessLogGroup")
        
        # API Gateway
        api = apigw.RestApi(self, 
                            'API', 
                            rest_api_name=f'{id}API',
                            deploy_options=apigw.StageOptions(
                                stage_name=environment,
                                logging_level=apigw.MethodLoggingLevel.ERROR,
                                access_log_destination=apigw.LogGroupLogDestination(rest_access_log_group),
                                access_log_format=apigw.AccessLogFormat.clf()
                            ))
        self.api = api
        self.api_url = api.url_for_path("/item")

        # Suppress CDK rule to allow using AWS managed policy AmazonAPIGatewayPushToCloudWatchLogs
        NagSuppressions.add_resource_suppressions(api, [
            { "id": 'AwsSolutions-IAM4', "reason": 'Allow using AWS managed policy AmazonAPIGatewayPushToCloudWatchLogs' },
        ], True)
        
        # Add "item" resource in the API
        api_resource_item = api.root.add_resource(
            'item',
            default_cors_preflight_options=apigw.CorsOptions(
                allow_methods=['PUT', 'POST', 'OPTIONS'],
                allow_origins=apigw.Cors.ALL_ORIGINS)
        )

        # Suppress CDK rule to allow OPTIONS be called without auth header
        NagSuppressions.add_resource_suppressions(api_resource_item, [
            { "id": 'AwsSolutions-APIG4', "reason": 'Allow OPTIONS to be called without auth header' },
        ], True)
        
        # API Gateway WebSocket
        ws_api = apigw2.CfnApi(self, "WebSocketAPI",
            name=f"{id}WSAPI",
            protocol_type="WEBSOCKET",
            route_selection_expression="$request.body.action"
        )
        self.ws_api = ws_api
        self.ws_api_endpoint = ws_api.attr_api_endpoint
        
        connect_route_key = "$connect"
        
        ws_connect_route = apigw2.CfnRoute(self, "ConnectRoute",
            api_id=ws_api.attr_api_id,
            route_key=connect_route_key,
            authorization_type="AWS_IAM",
            operation_name="ConnectRoute"
        )
        
        # ====== DATA LOADING ======
        insert_data_route_key = "insertdata"
        
        # Upload the vector insert query template to the bucket
        insert_query_template_upload = s3deploy.BucketDeployment(self, "S3InsertQueryUpload",
            sources=[s3deploy.Source.asset("./vector_insert_query.txt.zip")],
            destination_bucket= bucket,
            destination_key_prefix="query/"
        )
        
        # Define the Lambda function for REST API
        data_load_function = _lambda.Function(self, f'DataLoadingLambda',
           handler='lambda-handler.handler',
           runtime=_lambda.Runtime.PYTHON_3_12,
           code=_lambda.Code.from_asset('./lib/api/data_loading_lambda',
            bundling= BundlingOptions(
              image= _lambda.Runtime.PYTHON_3_12.bundling_image,
              command= [
                'bash',
                '-c',
                'pip install -r requirements.txt -t /asset-output && cp -au . /asset-output',
              ],
            )
           ),  
           vpc=vpc,
           vpc_subnets=private_with_egress_subnets,
           timeout=Duration.minutes(15),
           environment = {
                'DB_WRITER_ENDPOINT': db_writer_endpoint.hostname,
                'DATABASE_NAME': database_name,
                'TEMPLATE_BUCKET_NAME': bucket.bucket_name,
                'QUERY_TEMPLATE_OBJECT_PATH': "query/vector_insert_query.txt",
           }
        )
        bucket.grant_read(data_load_function)
        
        # Add permission to access Secrets Manager to the Lambda function
        statement = iam.PolicyStatement()
        statement.add_actions("secretsmanager:GetSecretValue")
        statement.add_resources(db_secret_arn)
        data_load_function.add_to_role_policy(statement)
        
        # Add permission to access Bedrock to the Lambda function
        statement = iam.PolicyStatement()
        statement.add_actions("bedrock:InvokeModel")
        statement.add_resources("*")
        data_load_function.add_to_role_policy(statement)
        
        # Add permission to send @connections as a response back to API Gateway WebSocket
        statement = iam.PolicyStatement()
        statement.add_actions("execute-api:ManageConnections")
        statement.add_resources(f"arn:aws:execute-api:{aws_region}:{aws_account_id}:{ws_api.attr_api_id}/*")
        data_load_function.add_to_role_policy(statement)
        
        # Add resource based policy to allow access from API Gateway WebSocket
        data_load_function.add_permission(id="AllowInsertFromAPIGWWS", 
                                          principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
                                          action="lambda:InvokeFunction",
                                          source_arn=f"arn:aws:execute-api:{aws_region}:{aws_account_id}:{ws_api.attr_api_id}/*/{insert_data_route_key}"
        )

        # Suppress CDK nag rule for using * in IAM policy since for flexibility in choosing Bedrock model and for calling routes in API Gateway WebSocket APIs
        # Suppress CDK nag rule for using managed policy AWSLambdaVPCAccessExecutionRole and AWSLambdaBasicExecutionRole
        NagSuppressions.add_resource_suppressions(data_load_function, [
            { "id": 'AwsSolutions-IAM5', "reason": 'Allow  * in the resources string for flexibility in choosing Bedrock model and in calling WebSocket APIs' },
            { "id": 'AwsSolutions-IAM4', "reason": 'Allow  using managed policy AWSLambdaVPCAccessExecutionRole and AWSLambdaBasicExecutionRole' }
        ], True)
        
        # API Gateway - Lambda integration
        data_load_api_lambda_integration = apigw.LambdaIntegration(
            data_load_function,
            integration_responses=[
                apigw.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        'method.response.header.Access-Control-Allow-Origin': "'*'"
                    }
                )
            ]
        )

        # Request model for data loading
        data_loading_request_model = api.add_model("DataLoadingRequestModel",
            content_type="application/json",
            model_name="DataLoadingRequestModel",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="dataLoadingRequest",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "text": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                    "additional_query_parameters": apigw.JsonSchema(type=apigw.JsonSchemaType.ARRAY)
                },
                required=["text"]
            )
        )

        # Request validator for the "PUT" method
        data_loading_request_validator = apigw.RequestValidator(self, "DataLoadingRequestValidator",
            rest_api=api,
            request_validator_name="data-loading-request-validator",
            validate_request_body=True,
            validate_request_parameters=True
        )
        
        # Add "PUT" method to the API "item" resource
        api_resource_item.add_method(
            'PUT', data_load_api_lambda_integration,
            authorization_type=apigw.AuthorizationType.IAM,
            method_responses=[
                apigw.MethodResponse(
                    status_code="200",
                    response_parameters={
                        'method.response.header.Access-Control-Allow-Origin': True
                    }
                )
            ],
            request_parameters={
                "method.request.querystring.db": False
            },
            request_models= {
                "application/json": data_loading_request_model,
            },
            request_validator=data_loading_request_validator
        )
        
        # API Gateway WS - Lambda integration
        ws_insert_data_integration = apigw2.CfnIntegration(self, "InsertDataIntegration",
            api_id=ws_api.attr_api_id,
            integration_type="AWS_PROXY",
            integration_uri=f"arn:aws:apigateway:{aws_region}:lambda:path/2015-03-31/functions/{data_load_function.function_arn}/invocations"
        )
        
        # API Gateway WS - Route
        ws_insert_data_route = apigw2.CfnRoute(self, "InsertDataRoute",
            api_id=ws_api.attr_api_id,
            route_key=insert_data_route_key,
            operation_name="InsertDataRoute",
            target=f"integrations/{ws_insert_data_integration.ref}"
        )

        # Suppress the nag rule to enable auth, because for API Gateway WebSocket, the auth is configured on $connect only, which will protect the other routes.
        NagSuppressions.add_resource_suppressions(ws_insert_data_route, [
            { "id": 'AwsSolutions-APIG4', "reason": 'For API Gateway Web Socket, the auth is configured on $connect only, which will protect other routes too.' }
        ])
        
        # ====== INFERENCE ======
        inference_route_key = "inference"
        
        # Upload the prompt template to the bucket
        prompt_template_upload = s3deploy.BucketDeployment(self, "S3PromptUpload",
            sources=[s3deploy.Source.asset("./prompt_template.txt.zip")],
            destination_bucket= bucket,
            destination_key_prefix="prompt/"
        )
        
        # Upload the vector search query template to the bucket
        search_query_template_upload = s3deploy.BucketDeployment(self, "S3QueryUpload",
            sources=[s3deploy.Source.asset("./vector_search_query.txt.zip")],
            destination_bucket= bucket,
            destination_key_prefix="query/"
        )
        
        # Parameter store for storing LLM parameter
        default_llm_parameters = { 
            "temperature": 0.7,
            "top_k": 1,
            "max_tokens_to_sample": 1000,
            "stop_sequences": ["\n\nHuman:"],
        }
        ssm_llm_parameter = ssm.StringParameter(self, "LLMParameters",
            parameter_name="llm",
            string_value=json.dumps(default_llm_parameters)
        )
        self.ssm_llm_parameter = ssm_llm_parameter
        
        # Parameter store for storing recommendation related parameter
        default_recommendation_parameters = { 
            "num_types": '1', # Number of item types that LLM should recommend given a profile/requirement.
            "num_items": '1', # Number of recommended items to be returned by vector DB during search.
            "model_id": "anthropic.claude-v2"
        }
        ssm_recommendation_parameter = ssm.StringParameter(self, "RecommendationParameters",
            parameter_name="recommendation",
            string_value=json.dumps(default_recommendation_parameters)
        )
        self.ssm_recommendation_parameter = ssm_recommendation_parameter
            
        # Define the Lambda function for REST API
        inference_function = _lambda.Function(self, f'InferenceLambda',
           handler='lambda-handler.handler',
           runtime=_lambda.Runtime.PYTHON_3_12,
           code=_lambda.Code.from_asset('./lib/api/inference_lambda',
            bundling= BundlingOptions(
              image= _lambda.Runtime.PYTHON_3_12.bundling_image,
              command= [
                'bash',
                '-c',
                'pip install -r requirements.txt -t /asset-output && cp -au . /asset-output',
              ],
            )
           ),                                   
           vpc=vpc,
           vpc_subnets=private_with_egress_subnets,
           timeout=Duration.minutes(5),
           environment = {
                'DB_READER_ENDPOINT': db_reader_endpoint.hostname,
                'TEMPLATE_BUCKET_NAME': bucket.bucket_name,
                'PROMPT_TEMPLATE_OBJECT_PATH': "prompt/prompt_template.txt",
                'QUERY_TEMPLATE_OBJECT_PATH': "query/vector_search_query.txt",
                'RECOMMENDATION_PARAMETER_NAME': ssm_recommendation_parameter.parameter_name,
                'LLM_PARAMETER_NAME': ssm_llm_parameter.parameter_name,
                "DATABASE_NAME": database_name
           }
        )
        bucket.grant_read(inference_function)
        
        # Add permission to access Secrets Manager to the Lambda function
        statement = iam.PolicyStatement()
        statement.add_actions("secretsmanager:GetSecretValue")
        statement.add_resources(db_secret_arn)
        inference_function.add_to_role_policy(statement)
        
        # Add permission to access Bedrock to the Lambda function
        statement = iam.PolicyStatement()
        statement.add_actions("bedrock:InvokeModel")
        statement.add_resources("*")
        inference_function.add_to_role_policy(statement)
        
        # Add permission to send @connections as a response back to API Gateway WebSocket
        statement = iam.PolicyStatement()
        statement.add_actions("execute-api:ManageConnections")
        statement.add_resources(f"arn:aws:execute-api:{aws_region}:{aws_account_id}:{ws_api.attr_api_id}/*")
        inference_function.add_to_role_policy(statement)
        
        # Add permission to access SSM parameter store for recommendation parameters to the Lambda function
        statement = iam.PolicyStatement()
        statement.add_actions("ssm:GetParameter")
        statement.add_resources(ssm_recommendation_parameter.parameter_arn)
        inference_function.add_to_role_policy(statement)
        
        # Add permission to access SSM parameter store for llm parameters to the Lambda function
        statement = iam.PolicyStatement()
        statement.add_actions("ssm:GetParameter")
        statement.add_resources(ssm_llm_parameter.parameter_arn)
        inference_function.add_to_role_policy(statement)
       
        # Add resource based policy to allow access from API Gateway WebSocket
        inference_function.add_permission(id="AllowInferenceFromAPIGWWS", 
                                          principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
                                          action="lambda:InvokeFunction",
                                          source_arn=f"arn:aws:execute-api:{aws_region}:{aws_account_id}:{ws_api.attr_api_id}/*/{inference_route_key}"
        )

        # Suppress CDK nag rule for using * in IAM policy since for flexibility in choosing Bedrock model and for calling routes in API Gateway WebSocket APIs
        # Suppress CDK nag rule for using managed policy AWSLambdaVPCAccessExecutionRole and AWSLambdaBasicExecutionRole
        NagSuppressions.add_resource_suppressions(inference_function, [
            { "id": 'AwsSolutions-IAM5', "reason": 'Allow  * in the resources string for flexibility in choosing Bedrock model and in calling WebSocket APIs' },
            { "id": 'AwsSolutions-IAM4', "reason": 'Allow  using managed policy AWSLambdaVPCAccessExecutionRole and AWSLambdaBasicExecutionRole' }
        ], True)
        
        # API Gateway - Lambda integration
        inference_api_lambda_integration = apigw.LambdaIntegration(
            inference_function,
            integration_responses=[
                apigw.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        'method.response.header.Access-Control-Allow-Origin': "'*'"
                    }
                )
            ]
        )

        # Request model for inference
        inference_request_model = api.add_model("InferenceRequestModel",
            content_type="application/json",
            model_name="InferenceRequestModel",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="inferenceRequest",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "text": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                    "num_items": apigw.JsonSchema(type=apigw.JsonSchemaType.INTEGER),
                    "num_types": apigw.JsonSchema(type=apigw.JsonSchemaType.INTEGER),
                    "additional_query_parameters": apigw.JsonSchema(type=apigw.JsonSchemaType.ARRAY),
                    "additional_prompt_parameters": apigw.JsonSchema(type=apigw.JsonSchemaType.ARRAY)
                },
                required=["text"]
            )
        )

        # Request validator for the "POST" method
        inference_request_validator = apigw.RequestValidator(self, "InferenceRequestValidator",
            rest_api=api,
            request_validator_name="inference-request-validator",
            validate_request_body=True,
            validate_request_parameters=True
        )
        
        # Add "POST" method to the API "item" resource
        api_resource_item.add_method(
            'POST', inference_api_lambda_integration,
            authorization_type=apigw.AuthorizationType.IAM,
            method_responses=[
                apigw.MethodResponse(
                    status_code="200",
                    response_parameters={
                        'method.response.header.Access-Control-Allow-Origin': True
                    }
                )
            ],
            request_parameters={
                "method.request.querystring.db": False
            },
            request_models={
                "application/json": inference_request_model
            },
            request_validator=inference_request_validator
        )
        
        # API Gateway WS - Lambda integration
        ws_inference_integration = apigw2.CfnIntegration(self, "InferenceIntegration",
            api_id=ws_api.attr_api_id,
            integration_type="AWS_PROXY",
            integration_uri=f"arn:aws:apigateway:{aws_region}:lambda:path/2015-03-31/functions/{inference_function.function_arn}/invocations"
        )
        
        # API Gateway WS - Route
        ws_inference_route = apigw2.CfnRoute(self, "InferenceRoute",
            api_id=ws_api.attr_api_id,
            route_key=inference_route_key,
            operation_name="InferenceRoute",
            target=f"integrations/{ws_inference_integration.ref}"
        )

        # Suppress the nag rule to enable auth, because for API Gateway WebSocket, the auth is configured on $connect only, which will protect the other routes.
        NagSuppressions.add_resource_suppressions(ws_inference_route, [
            { "id": 'AwsSolutions-APIG4', "reason": 'For API Gateway Web Socket, the auth is configured on $connect only, which will protect other routes too.' }
        ])
        
        # ====== WEB SOCKET API DEPLOYMENT AND STAGE ======
        
        # WS API Deployment
        ws_deployment = apigw2.CfnDeployment(self, "WSAPIDeployment",
            api_id=ws_api.attr_api_id
        )
        ws_deployment.add_dependency(ws_connect_route)
        ws_deployment.add_dependency(ws_insert_data_route)
        ws_deployment.add_dependency(ws_inference_route)

        # Log format
        log_format = {
            "requestId":"$context.requestId", 
            "ip": "$context.identity.sourceIp", 
            "caller":"$context.identity.caller", 
            "user":"$context.identity.user", 
            "requestTime":"$context.requestTime", 
            "eventType":"$context.eventType", 
            "routeKey":"$context.routeKey", 
            "status":"$context.status", 
            "connectionId":"$context.connectionId"
        }
        
        # WS API Stage
        stage_name = environment
        ws_stage = apigw2.CfnStage(self, "WSAPIStage",
            api_id=ws_api.attr_api_id,
            stage_name=stage_name,
            deployment_id=ws_deployment.attr_deployment_id,
            access_log_settings=apigw2.CfnStage.AccessLogSettingsProperty(
                destination_arn=ws_access_log_group.log_group_arn,
                format=json.dumps(log_format)
            ),
        )
        
        self.ws_api_stage = ws_stage.stage_name

        # Suppress the CDKBucketDeployment on Lambda runtime, * used in policy, and for using managed policy as they are managed by CDK
        NagSuppressions.add_resource_suppressions_by_path(self, '/Default/APIStack/Custom::CDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C', [
            { "id": 'AwsSolutions-L1', "reason": "The Lambda function's runtime is managed by CDK."},
            { "id": 'AwsSolutions-IAM4', "reason": 'The is managed by CDK'},
            { "id": 'AwsSolutions-IAM5', "reason": 'The is managed by CDK.'}
        ], True)
        
        # Suppress CDK rule requiring to use user pool authorizer for API Gateway, as IAM authorization is used.
        NagSuppressions.add_resource_suppressions(api_resource_item, [
            { "id": 'AwsSolutions-COG4', "reason": 'IAM authorization is used instead of Cognito user pool' },
        ], True)
        
        
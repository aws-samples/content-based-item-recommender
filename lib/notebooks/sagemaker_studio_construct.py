# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_iam as iam,
    aws_efs as efs,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_sagemaker as sagemaker,
    RemovalPolicy, Duration, CustomResource, BundlingOptions
) 
from aws_cdk.custom_resources import Provider
from constructs import Construct
from .helpers import get_sagemaker_image_arn
from typing import List
from cdk_nag import NagSuppressions
import copy

JUPYTER_LAB_APP_IMAGE_NAME = "sagemaker-distribution-cpu"
JUPYTER_SERVER_APP_IMAGE_NAME = "jupyter-server-3"
KERNEL_GATEWAY_APP_IMAGE_NAME = "sagemaker-data-science-310-v1"

class SageMakerStudio(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        vpc: ec2.Vpc,
        bucket: s3.Bucket,
        api: apigw.RestApi,
        ws_api_id: str,
        ws_api_endpoint: str,
        ws_api_stage: str,
        aws_region: str,
        aws_account_id: str,
        db_writer_endpoint: str,
        db_reader_endpoint: str,
        db_secret_arn: str,
        ssm_llm_parameter,
        ssm_recommendation_parameter,
        bastion_host_asg_name: str,
        user_name: str = "setup-user",
        default_instance_type: str = "ml.t3.medium"
    ):
        super().__init__(scope, id)    
        
        private_subnets = [subnet.subnet_id for subnet in vpc.private_subnets]

        # Studio Lifecycle Policy - Lambda custom resource
        lifecycle_policy_setup_role = iam.Role(
            id="StudioLifecycleLambdaCR",
            scope=self,
            role_name=f"{id}StudioLifecycleLambdaCRRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "SagemakerStudioLifeCycleConfigProvisioningPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=[
                                "sagemaker:CreateStudioLifecycleConfig",
                                "sagemaker:DeleteStudioLifecycleConfig",
                            ],
                            resources=[
                                f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:studio-lifecycle-config/*"
                            ],
                            effect=iam.Effect.ALLOW,
                        )
                    ]
                )
            },
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Suppress it for using * in IAM policy since the lifecycle config is created by custom-resource so full ID is unknown
        # Also suppress it for using managed policy AWSLambdaBasicExecutionRole for CloudWatch logging.
        NagSuppressions.add_resource_suppressions(lifecycle_policy_setup_role, [
            { "id": 'AwsSolutions-IAM4', "reason": 'AWSLambdaBasicExecutionRole is for CloudWatch logging'},
            { "id": 'AwsSolutions-IAM5', "reason": 'The * is needed for the lifecycle config'}
        ], True)

        lifecycle_policy_setup_lambda = _lambda.Function(
            scope=self,
            id="StudioLifecycleLambda",
            function_name=f"{id}StudioLifecycleLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            code=_lambda.Code.from_asset("./lib/notebooks/setup_lifecycle"),
            handler="index.on_event",
            role=lifecycle_policy_setup_role,
            timeout=Duration.minutes(5),
        )

        lifecycle_policy_setup_provider = Provider(
            scope=self,
            id="StudioLifecycleCRProvider",
            provider_function_name=f"{id}StudioLifecycleCRProvider",
            on_event_handler=lifecycle_policy_setup_lambda,
        )

        # Suppress cdk_nag rule for using not the latest runtime for non container Lambda, as this is managed by CDK Provider.
        # Also suppress it for using * in IAM policy and for using managed policy, as this is managed by CDK Provider.
        NagSuppressions.add_resource_suppressions(lifecycle_policy_setup_provider, [
            { "id": 'AwsSolutions-L1', "reason": "The Lambda function's runtime is managed by CDK Provider. Solution is to update CDK version."},
            { "id": 'AwsSolutions-IAM4', "reason": 'The Lambda function is managed by Provider'},
            { "id": 'AwsSolutions-IAM5', "reason": 'The Lambda function is managed by Provider.'}
        ], True)

        lifecycle_policy_custom_resource = CustomResource(
            scope=self,
            id=f"StudioLifecycleCR",
            service_token=lifecycle_policy_setup_provider.service_token,
            removal_policy=RemovalPolicy.DESTROY,
            resource_type="Custom::StudioLifecycleConfig",
            properties={
                "id_prefix": id,
                "bucket_name": bucket.bucket_name,
                "api_url": api.url_for_path("/item"),
                "ws_api_endpoint": ws_api_endpoint,
                "ws_api_stage": ws_api_stage,
                "db_writer_endpoint": db_writer_endpoint.hostname,
                "db_reader_endpoint": db_reader_endpoint.hostname,
                "ssm_llm_parameter_name": ssm_llm_parameter.parameter_name,
                "ssm_recommendation_parameter_name": ssm_recommendation_parameter.parameter_name,
                "bastion_host_asg_name": bastion_host_asg_name
            }
        )

        # Inline policy for Studio role
        studio_role_policy = {
            "ec2-sagemaker": iam.PolicyDocument(
                statements=[iam.PolicyStatement(
                    actions=["ec2:CreateVpcEndpoint","ec2:DescribeRouteTables"],
                    resources=[
                        f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:vpc-endpoint/*",
                        f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:route-table/*"
                    ]
                )]
            ),
            "autoscaling": iam.PolicyDocument(
                statements=[iam.PolicyStatement(
                    actions=["autoscaling:DescribeAutoScalingGroups"],
                    resources=["*"] # This API does not support resource-level IAM permission so wildcard is needed.
                )]
            ),
            "s3": iam.PolicyDocument(
                statements=[iam.PolicyStatement(
                    actions=["s3:ListBucket", "s3:GetObject", "s3:PutObject"],
                    resources=[bucket.bucket_arn, bucket.arn_for_objects("*")]
                )]
            ),
            "apigw": iam.PolicyDocument(
                statements=[iam.PolicyStatement(
                    actions=["execute-api:Invoke"],
                    resources=[
                        api.arn_for_execute_api(method="POST", path="/item"),
                        api.arn_for_execute_api(method="PUT", path="/item"),
                        api.arn_for_execute_api(method="OPTIONS", path="/item"),
                        f"arn:aws:execute-api:{aws_region}:{aws_account_id}:{ws_api_id}/*/*" 
                    ]
                )]
            ),
            "secretsmanager": iam.PolicyDocument(
                statements=[iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[db_secret_arn]
                )]
            ),
            "bedrock": iam.PolicyDocument(
                statements=[iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[f"arn:aws:bedrock:{aws_region}::foundation-model/*"]
                )]
            ),
            "ssm": iam.PolicyDocument(
                statements= [iam.PolicyStatement(
                    actions=["ssm:GetParameter", "ssm:PutParameter"],
                    resources=[
                        ssm_llm_parameter.parameter_arn, 
                        ssm_recommendation_parameter.parameter_arn
                    ]
                )]
            ),
            "ssm-command": iam.PolicyDocument(
                statements= [iam.PolicyStatement(
                    actions=["ssm:SendCommand", "ssm:GetCommandInvocation"],
                    resources=[
                        f"arn:aws:ec2:{aws_region}:{aws_account_id}:instance/*",
                        f"arn:aws:ssm:{aws_region}::document/AWS-RunShellScript",
                        f"arn:aws:ssm:{aws_region}:{aws_account_id}:*"
                    ]
                )]
            )
        }
        
        # IAM Role for Studio
        sagemaker_studio_execution_role = iam.Role(
            self,
            "SagemakerStudioExecutionRole",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            inline_policies=studio_role_policy
        )

        # Suppress cdk_nag rule for using * in resources in the policy.
        NagSuppressions.add_resource_suppressions(sagemaker_studio_execution_role, [
            { "id": 'AwsSolutions-IAM5', "reason": 'Flexibility needed in choosing Bedrock models, uploading/reading files to the dedicated S3 bucket.'}
        ], True)
        
        # Studio Security Group
        studio_security_group = ec2.SecurityGroup(
            self,
            "StudioSecurityGroup",
            security_group_name=f"{id}SagemakerStudio",
            vpc=vpc,
            description="Inbound and outbound for all from itself, and outbound to EFS",
            allow_all_outbound=True,
        )
        
        studio_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(cidr_ip=vpc.vpc_cidr_block),
            connection=ec2.Port.all_tcp(),
        )
       
        # Studio Domain
        sagemaker_studio_domain = sagemaker.CfnDomain(
            self,
            "SageMakerStudioDomain",
            auth_mode="IAM",
            default_user_settings=sagemaker.CfnDomain.UserSettingsProperty(
                execution_role=sagemaker_studio_execution_role.role_arn,           
                security_groups=[studio_security_group.security_group_id],
                sharing_settings=sagemaker.CfnDomain.SharingSettingsProperty(
                    notebook_output_option="Disabled"
                ),
                jupyter_lab_app_settings=sagemaker.CfnDomain.JupyterLabAppSettingsProperty(
                    default_resource_spec=sagemaker.CfnDomain.ResourceSpecProperty(
                        instance_type="ml.t3.medium",
                        lifecycle_config_arn=lifecycle_policy_custom_resource.ref,
                        sage_maker_image_arn=get_sagemaker_image_arn(
                            JUPYTER_LAB_APP_IMAGE_NAME, aws_region
                        )
                    ),
                    lifecycle_config_arns=[lifecycle_policy_custom_resource.ref]
                ),
                space_storage_settings=sagemaker.CfnDomain.DefaultSpaceStorageSettingsProperty(
                    default_ebs_storage_settings=sagemaker.CfnDomain.DefaultEbsStorageSettingsProperty(
                        default_ebs_volume_size_in_gb=30,
                        maximum_ebs_volume_size_in_gb=300
                    )
                ),
                studio_web_portal="ENABLED"
            ),
            domain_name=f"{id}Studio",
            subnet_ids=private_subnets,
            vpc_id=vpc.vpc_id,
            app_network_access_type="VpcOnly",
        )
        
        sagemaker_studio_domain.node.add_dependency(lifecycle_policy_custom_resource)
        sagemaker_studio_domain.apply_removal_policy(policy=RemovalPolicy.DESTROY, apply_to_update_replace_policy=False)

        studio_user_role_policy = copy.deepcopy(studio_role_policy)
        studio_user_role_policy['sagemaker'] = iam.PolicyDocument(
            statements=[iam.PolicyStatement(
                actions=[
                    "sagemaker:ListSpaces",
                    "sagemaker:DescribeSpace", 
                    "sagemaker:CreateSpace", 
                    "sagemaker:UpdateSpace",
                    "sagemaker:DeleteSpace", 
                    "sagemaker:ListApps",
                    "sagemaker:CreateApp", 
                    "sagemaker:DescribeApp", 
                    "sagemaker:DeleteApp", 
                    "sagemaker:ListStudioLifecycleConfigs",
                    "sagemaker:DescribeStudioLifecycleConfig",
                    "sagemaker:CreateStudioLifecycleConfig",
                    "sagemaker:DeleteStudioLifecycleConfig",
                    "sagemaker:ListUserProfiles",
                    "sagemaker:DescribeUserProfile",
                    "sagemaker:DescribeDomain",
                    "sagemaker:CreatePresignedDomainUrl"
                    
                ],
                resources=[
                    f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:app/{sagemaker_studio_domain.attr_domain_id}/*",
                    f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:space/{sagemaker_studio_domain.attr_domain_id}/*",
                    f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:user-profile/{sagemaker_studio_domain.attr_domain_id}/*",
                    f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:studio-lifecycle-config/{sagemaker_studio_domain.attr_domain_id}/*",
                    f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:domain/{sagemaker_studio_domain.attr_domain_id}",
                ],
            )]
        )

        # IAM Role for Studio user profile
        sagemaker_studio_user_execution_role = iam.Role(
            self,
            "SagemakerStudioUserExecutionRole",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            inline_policies=studio_user_role_policy
        )

        # Suppress cdk_nag rule for using * in resources in the policy.
        NagSuppressions.add_resource_suppressions(sagemaker_studio_user_execution_role, [
            { "id": 'AwsSolutions-IAM5', "reason": 'Allowing users to perform actions on app, space, user-profile, studio-lifecycle-config, within domain'}
        ], True)
        
        # Studio User Profiles
        user_profile = sagemaker.CfnUserProfile(
            self,
            "SageMakerStudioUserProfile_" + user_name,
            domain_id=sagemaker_studio_domain.attr_domain_id,
            user_profile_name=user_name,
            user_settings=sagemaker.CfnUserProfile.UserSettingsProperty(
                execution_role=sagemaker_studio_user_execution_role.role_arn,
            )
        )
      
        # The code below is for custom resource to set up space and JupyterLab app
        domain_resources_setup_role = iam.Role(
            id="DomainResourcesLambdaCR",
            scope=self,
            role_name=f"{id}DomainResourcesLambdaCRRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "DomainResourcesProvisioningPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=[
                                "sagemaker:CreateSpace",
                                "sagemaker:ListSpaces",
                                "sagemaker:DescribeSpace",
                                "sagemaker:DeleteSpace",
                                "sagemaker:CreateApp",
                                "sagemaker:ListApps",
                                "sagemaker:DescribeApp",
                                "sagemaker:DeleteApp",
                                "sagemaker:ListUserProfiles",
                                "sagemaker:DescribeUserProfile",
                                "sagemaker:DeleteUserProfile",
                                "sagemaker:DescribeDomain",
                                "sagemaker:DeleteDomain",
                            ],
                            resources=[
                                f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:app/*",
                                f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:space/*",
                                sagemaker_studio_domain.attr_domain_arn,
                                f"{sagemaker_studio_domain.attr_domain_arn}/*",
                                f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:user-profile/*",
                                f"arn:aws:sagemaker:{aws_region}:{aws_account_id}:domain/*",
                            ],
                            effect=iam.Effect.ALLOW,
                        )
                    ]
                )
            },
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Suppres cdk_nag rule for using managed policy as AWSLambdaBasicExecutionRole only contains CloudWatch Logs permissions
        NagSuppressions.add_resource_suppressions(domain_resources_setup_role, [
            { "id": 'AwsSolutions-IAM4', "reason": 'Allow using managed policy for Lambda basic execution role' }
        ])

        # Suppress cdk_nag rule for having partial * in resources, since user can create new SageMaker user profile, spaces, and apps after the CFN deployment.
        # The template is intended to delete SageMaker user profiles, spaces, and apps of this domain, on CFN delete.
        NagSuppressions.add_resource_suppressions(domain_resources_setup_role, [
            { "id": 'AwsSolutions-IAM5', "reason": 'Allow partial * in the resources string to ensure the components inside SageMaker domain is deleted on CFN delete' }
        ])

        domain_resources_setup_lambda = _lambda.Function(
            scope=self,
            id="DomainResourcesLambda",
            function_name=f"{id}DomainResourcesLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            code=_lambda.Code.from_asset("./lib/notebooks/setup_domain_resources", bundling= BundlingOptions(
                  image= _lambda.Runtime.PYTHON_3_12.bundling_image,
                  command= [
                    'bash',
                    '-c',
                    'pip install -r requirements.txt -t /asset-output && cp -au . /asset-output',
                  ],
                )),
            handler="index.on_event",
            role=domain_resources_setup_role,
            timeout=Duration.minutes(5),
        )

        domain_resources_setup_provider = Provider(
            scope=self,
            id="DomainResourcesCRProvider",
            provider_function_name=f"{id}DomainResourcesCRProvider",
            on_event_handler=domain_resources_setup_lambda,
        )

        # Suppress cdk_nag rule for using not the latest runtime for non container Lambda, as this is managed by CDK Provider.
        # Also suppress it for using * in IAM policy and for using managed policy, as this is managed by CDK Provider.
        NagSuppressions.add_resource_suppressions(domain_resources_setup_provider, [
            { "id": 'AwsSolutions-L1', "reason": "The Lambda function's runtime is managed by CDK Provider. Solution is to update CDK version."},
            { "id": 'AwsSolutions-IAM4', "reason": 'The Lambda function is managed by Provider'},
            { "id": 'AwsSolutions-IAM5', "reason": 'The Lambda function is managed by Provider.'}
        ], True)

        domain_resources_custom_resource = CustomResource(
            scope=self,
            id=f"DomainResourcesCR",
            service_token=domain_resources_setup_provider.service_token,
            removal_policy=RemovalPolicy.DESTROY,
            resource_type="Custom::StudioDomainResources",
            properties={
                "domain_id": sagemaker_studio_domain.attr_domain_id,
                "user_profile_name": user_profile.user_profile_name,
                "id_prefix": id,
                "image_arn": get_sagemaker_image_arn(JUPYTER_LAB_APP_IMAGE_NAME, aws_region),
                "version_alias": "1.2.0",
                "lcc_arn": lifecycle_policy_custom_resource.ref
            }
        )
        
        domain_resources_custom_resource.node.add_dependency(sagemaker_studio_domain)
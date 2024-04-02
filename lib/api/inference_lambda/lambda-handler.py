import os, json
import boto3
import psycopg2, psycopg2.extras

bedrock = boto3.client("bedrock-runtime")
s3 = boto3.client('s3')
ssm = boto3.client('ssm')

reader_endpoint = os.environ['DB_READER_ENDPOINT']
template_bucket_name = os.environ['TEMPLATE_BUCKET_NAME']
prompt_template_object_path = os.environ['PROMPT_TEMPLATE_OBJECT_PATH']
query_template_object_path = os.environ['QUERY_TEMPLATE_OBJECT_PATH']
ssm_recommendation_parameter_name = os.environ['RECOMMENDATION_PARAMETER_NAME']
ssm_llm_parameter_name = os.environ['LLM_PARAMETER_NAME']
database_name = os.environ['DATABASE_NAME']
    
class Database():
    def __init__(self, reader, database_name, port=5432):
        self.reader_endpoint = reader
        self.username = None
        self.password = None
        self.database_name=database_name
        self.port = port
        self.conn = None
    
    def fetch_credentials(self):
        secrets_manager = boto3.client('secretsmanager')
        credentials = json.loads(secrets_manager.get_secret_value(
            SecretId='AuroraClusterCredentials'
        )["SecretString"])
        self.username = credentials["username"]
        self.password = credentials["password"]
    
    def connect_for_reading(self):
        if self.username is None or self.password is None: self.fetch_credentials()
            
        conn = psycopg2.connect(host=self.reader_endpoint, port=self.port, user=self.username, password=self.password, database=self.database_name)
        conn.autocommit = True
        self.conn = conn
        return conn
    
    def close_connection(self):
        self.conn.close()
        self.conn = None
    
    def search(self, query_template, embedding, num_items=1, additional_query_parameters=[]):
        if self.conn is None:
            self.connect_for_reading()
        
        all_query_parameters = [embedding, str(num_items)] + additional_query_parameters
        query_statement = query_template.format(*all_query_parameters)
        
        cur = self.conn.cursor(cursor_factory = psycopg2.extras.RealDictCursor)
        # Disabling semgrep rule for raw query as this is meant to be run by admin/engineer with authentication
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        cur.execute(query_statement)
        results = cur.fetchall()
        cur.close()
        print(f'Query response: {results}')
        return results

db = Database(reader=reader_endpoint, database_name=database_name)
db.connect_for_reading()

ssm_llm_parameters = json.loads(ssm.get_parameter(
    Name=ssm_llm_parameter_name
)['Parameter']['Value'])
ssm_recommendation_parameters = json.loads(ssm.get_parameter(
    Name=ssm_recommendation_parameter_name
)['Parameter']['Value'])

def handler(event, context):
    print(event)
    
    additional_query_parameters = []
    additional_prompt_parameters = []

    mode = "rest"
    if ('requestContext' in event) and ('routeKey' in event['requestContext']): mode = "websocket"
    
    event_body = event['body']
    if len(event_body) > 200000:
        return {
            "statusCode": 400,
            'body': 'Event body is too large'
        }
    # Disabling semgrep rule for checking data size to be loaded to JSON as the check is already done right above.
    # nosemgrep: python.aws-lambda.deserialization.tainted-json-aws-lambda.tainted-json-aws-lambda
    event_body = json.loads(event_body)
    input_text = event_body['text']

    if 'num_items' in event_body:
        ssm_recommendation_parameters['num_items'] = event_body['num_items']
    if 'num_types' in event_body:
        ssm_recommendation_parameters['num_types'] = event_body['num_types']
    if 'additional_query_parameters' in event_body:
        additional_query_parameters = event_body['additional_query_parameters']
    if 'additional_prompt_parameters' in event_body:
        additional_prompt_parameters = event_body['additional_prompt_parameters']
    
    # Substitute placeholders in the prompt with real values
    prompt_template = s3.get_object(Bucket=template_bucket_name, Key=prompt_template_object_path)['Body'].read().decode("utf-8")
    all_prompt_parameters = [input_text, str(ssm_recommendation_parameters['num_types'])] + additional_prompt_parameters
    prompt = prompt_template.format(*all_prompt_parameters)
    
    # TODO: Parallelize the below with call to first LLM
    # Download vector search query template from S3
    query_template = s3.get_object(Bucket=template_bucket_name, Key=query_template_object_path)['Body'].read().decode("utf-8")
    
    # Merge prompt with the LLM parameters
    ssm_llm_parameters['prompt'] = prompt
    body = json.dumps(ssm_llm_parameters)
    
    # Get the recommended item text from LLM
    response = bedrock.invoke_model(body=body, modelId=ssm_recommendation_parameters['model_id'])
    
    # Post-process suggested item types where it can be more than 1.
    # Disabling semgrep rule for checking data size to be loaded to JSON as the source is from Amazon Bedrock
    # nosemgrep: python.aws-lambda.deserialization.tainted-json-aws-lambda.tainted-json-aws-lambda
    recommended_item_types = json.loads(response.get("body").read())["completion"]
    recommended_item_types = recommended_item_types.split("###") if "\n###" in recommended_item_types else [recommended_item_types]
    recommended_item_types = list(filter(lambda x: x != '' and not x.isspace(), recommended_item_types))
    
    recommended_item_embeddings = []
    
    # Call the text-to-embedding model to get the embedding for each of the suggested item types.
    for item_type in recommended_item_types:
        # Get the embedding of the recommended item text
        body = json.dumps(
            {
                "inputText": item_type,
            }
        )

        response = bedrock.invoke_model(body=body, modelId="amazon.titan-embed-text-v1")
        # Disabling semgrep rule for checking data size to be loaded to JSON as the source is from Amazon Bedrock
        # nosemgrep: python.aws-lambda.deserialization.tainted-json-aws-lambda.tainted-json-aws-lambda
        recommended_item_embeddings.append(json.loads(response.get("body").read())["embedding"])

    recommended_items = []
    
    # Do search on vector database 
    try:
        for embedding in recommended_item_embeddings:
            recommended_items = recommended_items + db.search(query_template, 
                                          embedding, 
                                          num_items=ssm_recommendation_parameters['num_items'])
    except Exception as e:
        print("An exception happened when doing the search on the vector database")
        print(e)
    finally: 
        db.close_connection()

    # Deduplicate
    final_recommended_items = {}
    for item in sorted(recommended_items, key = lambda k: k["distance"]):
        if item['id'] not in final_recommended_items: final_recommended_items[item['id']] = item

    final_recommended_items = list({'id': v[1]['id'], 'distance': v[1]['distance'], 'description': v[1]['description']} for v in final_recommended_items.items())
    
    if mode == "websocket":
        domain = event['requestContext']['domainName']
        stage = event['requestContext']['stage']
        connection_id = event['requestContext']['connectionId']
        callback_url = f"https://{domain}/{stage}"
        apigw = boto3.client('apigatewaymanagementapi', endpoint_url= callback_url)

        response = apigw.post_to_connection(
            Data=bytes(json.dumps({
                "items": final_recommended_items
            }), "utf-8"),
            ConnectionId=connection_id
        )
        return {
            "statusCode": 200
        }
    
    response = {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps({
            "items": final_recommended_items
        })
    }

    return response
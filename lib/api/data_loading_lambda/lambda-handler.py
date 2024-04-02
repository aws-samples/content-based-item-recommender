import os, json
import boto3
import psycopg2

s3 = boto3.client('s3')
bedrock = boto3.client("bedrock-runtime")

writer_endpoint = os.environ['DB_WRITER_ENDPOINT']
database_name = os.environ['DATABASE_NAME']
template_bucket_name = os.environ['TEMPLATE_BUCKET_NAME']
query_template_object_path = os.environ['QUERY_TEMPLATE_OBJECT_PATH']

class Database():
    def __init__(self, writer, database_name, embedding_dimension=1536, port=5432):
        self.writer_endpoint = writer
        self.username = None
        self.password = None
        self.port = port
        self.database_name = database_name
        self.conn = None
    
    def fetch_credentials(self):
        secrets_manager = boto3.client("secretsmanager")
        credentials = json.loads(secrets_manager.get_secret_value(
            SecretId='AuroraClusterCredentials'
        )["SecretString"])
        self.username = credentials["username"]
        self.password = credentials["password"]
    
    def connect_for_writing(self):
        if self.username is None or self.password is None: self.fetch_credentials()
        
        conn = psycopg2.connect(host=self.writer_endpoint, port=self.port, user=self.username, password=self.password, database=database_name)
        conn.autocommit = True
        self.conn = conn
        return conn
    
    def close_connection(self):
        self.conn.close()
        self.conn = None
        
    
    def insert_vector(self, query_template, text, embedding, additional_query_parameters=[]):
        if self.conn is None:
            self.connect_for_writing()
            
        text = psycopg2.extensions.adapt(text)
        
        all_query_parameters = [text, str(embedding)] + additional_query_parameters
        query_statement = query_template.format(*all_query_parameters)

        cur = self.conn.cursor()

        # Disabling semgrep rule for raw query as this is meant to be run by admin/engineer with authentication
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        response = cur.execute(query_statement)
        cur.close()
        self.conn.commit()

        return response
    
db = Database(writer=writer_endpoint, database_name=database_name)
db.connect_for_writing()

def handler(event, context):
    print(event)
    
    additional_query_parameters = []
    
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
    item_text = event_body['text']
    
    if 'additional_query_parameters' in event_body:
        additional_query_parameters = event_body['additional_query_parameters']
    
    query_template = s3.get_object(Bucket=template_bucket_name, Key=query_template_object_path)['Body'].read().decode("utf-8")
    
    body = json.dumps(
        {
            "inputText": item_text,
        }
    )

    response = bedrock.invoke_model(body=body, modelId="amazon.titan-embed-text-v1")
    # Disabling semgrep rule for checking data size to be loaded to JSON as the source is from Amazon Bedrock
    # nosemgrep: python.aws-lambda.deserialization.tainted-json-aws-lambda.tainted-json-aws-lambda
    embedding = json.loads(response.get("body").read())["embedding"]
    
    try:
        db.insert_vector(query_template, 
                     item_text, 
                     embedding, 
                     additional_query_parameters=additional_query_parameters)
    except Exception as e:
        print("An error happens when inserting the vector into database")
        print(e)
    finally: 
        db.close_connection()
    
    if mode == "websocket":
        domain = event['requestContext']['domainName']
        stage = event['requestContext']['stage']
        connection_id = event['requestContext']['connectionId']
        callback_url = f"https://{domain}/{stage}"
        apigw = boto3.client('apigatewaymanagementapi', endpoint_url= callback_url)

        response = apigw.post_to_connection(
            Data=bytes('Data has been loaded', "utf-8"),
            ConnectionId=connection_id
        )
        return {
            "statusCode": 200
        }
    
    response = {
        "statusCode": 200,
        'body': 'Data has been loaded'
    }

    return response
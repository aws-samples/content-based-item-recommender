import json, os
import boto3
import psycopg2

database_name = os.environ['DATABASE_NAME']
embedding_dimension = os.environ["EMBEDDING_DIMENSION"]
writer_endpoint = os.environ['DB_WRITER_ENDPOINT']

class Database():
    def __init__(self, writer, database_name, embedding_dimension, port=5432):
        self.writer_endpoint = writer
        self.username = None
        self.password = None
        self.port = port
        self.database_name = database_name
        self.embedding_dimension = embedding_dimension
        self.conn = None
    
    def fetch_credentials(self):
        secrets_manager = boto3.client('secretsmanager')
        credentials = json.loads(secrets_manager.get_secret_value(
            SecretId='AuroraClusterCredentials'
        )["SecretString"])
        self.username = credentials["username"]
        self.password = credentials["password"]
    
    def connect_for_writing(self):
        if self.username is None or self.password is None: self.fetch_credentials()
            
        conn = psycopg2.connect(host=self.writer_endpoint, port=self.port, user=self.username, password=self.password, database=self.database_name)
        self.conn = conn
        return conn
    
    def close_connection(self):
        self.conn.close()
        self.conn = None
    
    def setup_vector_db(self, embedding_dimension):
        if self.conn is None:
            self.connect_for_writing()
        
        cur = self.conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("DROP TABLE IF EXISTS items;")
        # Disable semgrep rule for flagging formatted query as this Lambda is to be invoked in deployment phase by CloudFormation, not user facing.
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        cur.execute(f"CREATE TABLE items (id bigserial PRIMARY KEY, description text, embedding vector({str(embedding_dimension)}));")
        self.conn.commit()
        cur.close()
        return True
    
db = Database(writer=writer_endpoint, database_name = database_name, embedding_dimension = embedding_dimension)
    
def on_event(event, context):
    print(event)
    request_type = event['RequestType'].lower()
    if request_type == 'create':
        return on_create(event)
    if request_type == 'update':
        return on_update(event)
    if request_type == 'delete':
        return on_delete(event)
    raise Exception(f'Invalid request type: {request_type}')


def on_create(event):
    #props = event["ResourceProperties"]
    #print(props)
    #print(f'create new resource with {props}')
    
    try:
        db.setup_vector_db(embedding_dimension)
        db.close_connection()
    except Exception as e:
        print(e)
    
    return {'PhysicalResourceId': "VectorDBDatabaseSetup"}


def on_update(event):
    physical_id = event["PhysicalResourceId"]
    print("no op")
    return {'PhysicalResourceId': physical_id}

def on_delete(event):
    physical_id = event["PhysicalResourceId"]
    print("no op")
    return {'PhysicalResourceId': physical_id}

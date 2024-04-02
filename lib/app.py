#!/usr/bin/env python3

import os
from aws_cdk import ( 
    aws_apigateway as apigw,
    App, Stack, CfnOutput, Environment, CfnParameter, Aspects
)
from cdk_nag import AwsSolutionsChecks


from common.common_stack import CommonStack
from vectordb.vectordb_stack import VectorDBStack
from api.api_stack import APIStack
from notebooks.notebooks_stack import NotebooksStack

database_name = "vectordb"
account=os.environ["CDK_DEPLOY_ACCOUNT"]
region=os.environ["CDK_DEPLOY_REGION"]
allowed_regions = ["us-east-1", "us-west-2"]

print(f"Account ID: {account}")
print(f"Region: {region}")

try:
    if region not in allowed_regions:
        raise AssertionError
except AssertionError:
    print(f"Selected region is {region}. Please use only one of these regions {str(allowed_regions)}")

class RecommenderStack(Stack):
    def __init__(self, scope, **kwargs):
        super().__init__(scope, **kwargs)

        Aspects.of(self).add(AwsSolutionsChecks(verbose=True));
        
        deploy_bastion_host = self.node.try_get_context("deployBastionHost")
        deploy_bastion_host = True if deploy_bastion_host == "true" else False
        
        environment = self.node.try_get_context("environment") 
        print("Deploy bastion host?")
        print(deploy_bastion_host)
        print("Environment name:")
        print(environment)
        
        common =  CommonStack(self, "CommonStack")
        
        vector_db = VectorDBStack(self, "VectorDBStack", 
            vpc= common.vpc, 
            private_subnets= common.private_subnets,
            private_with_egress_subnets= common.private_with_egress_subnets,
            embedding_dimension=1536,  # For Titan Embedding model, set to 1536
            database_name=database_name,
            deploy_bastion_host=deploy_bastion_host
        )
        api = APIStack(self, "APIStack", 
            vpc=common.vpc, 
            private_with_egress_subnets= common.private_with_egress_subnets,
            bucket=common.bucket,
            db_writer_endpoint=vector_db.db_writer_endpoint,
            db_reader_endpoint=vector_db.db_reader_endpoint,
            database_name=database_name,
            db_secret_arn=vector_db.db_secret_arn,
            environment=environment
        )
        notebooks = NotebooksStack(self, "NotebooksStack", 
                                   vpc=common.vpc, 
                                   bucket=common.bucket, 
                                   api=api.api,
                                   ws_api_id = api.ws_api.attr_api_id,
                                   ws_api_endpoint=api.ws_api_endpoint,
                                   ws_api_stage=api.ws_api_stage,
                                   db_writer_endpoint=vector_db.db_writer_endpoint,
                                   db_reader_endpoint=vector_db.db_reader_endpoint,
                                   db_secret_arn=vector_db.db_secret_arn,
                                   ssm_llm_parameter=api.ssm_llm_parameter,
                                   ssm_recommendation_parameter=api.ssm_recommendation_parameter,
                                   bastion_host_asg_name=vector_db.bastion_host_asg_name if deploy_bastion_host else ""
                                  )
        notebooks.add_dependency(vector_db)
                                 

        CfnOutput(self, "db_writer_endpoint", value=vector_db.db_writer_endpoint.hostname)
        CfnOutput(self, "db_reader_endpoint", value=vector_db.db_reader_endpoint.hostname)
        CfnOutput(self, "bucket_name", value=common.bucket.bucket_name)
        CfnOutput(self, "api_url", value = api.api_url)
        CfnOutput(self, "ws_api_endpoint", value = api.ws_api_endpoint)
        CfnOutput(self, "ws_api_stage", value = api.ws_api_stage)
        CfnOutput(self, "bastion_host_asg_name", value=vector_db.bastion_host_asg_name if deploy_bastion_host else "")

app = App()
RecommenderStack(app, stack_name="RecommenderStack", env=Environment(
    account=account,
    region=region
))

app.synth()


# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    aws_ec2 as ec2,
    aws_s3_deployment as s3deploy,
    NestedStack, Stack
)
from constructs import Construct
from .sagemaker_studio_construct import SageMakerStudio
from cdk_nag import NagSuppressions


class NotebooksStack(NestedStack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc,
        bucket,
        api,
        ws_api_id: str,
        ws_api_endpoint: str,
        ws_api_stage: str,
        db_writer_endpoint,
        db_reader_endpoint,
        db_secret_arn: str,
        ssm_llm_parameter,
        ssm_recommendation_parameter,
        bastion_host_asg_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        aws_region = Stack.of(self).region
        aws_account_id = Stack.of(self).account
        
        # Upload notebook files to the bucket
        notebook_files_upload = s3deploy.BucketDeployment(self, "NotebookFilesUpload",
            sources=[s3deploy.Source.asset("./notebooks.zip")],
            destination_bucket= bucket,
            destination_key_prefix="notebooks/"
        )

        # Suppress the CDKBucketDeployment on Lambda runtime, * used in policy, and for using managed policy as they are managed by CDK
        NagSuppressions.add_resource_suppressions_by_path(self, '/Default/NotebooksStack/Custom::CDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C', [
            { "id": 'AwsSolutions-L1', "reason": "The Lambda function's runtime is managed by CDK."},
            { "id": 'AwsSolutions-IAM4', "reason": 'The is managed by CDK'},
            { "id": 'AwsSolutions-IAM5', "reason": 'The is managed by CDK.'}
        ], True)
        
        SageMakerStudio(self, f"{construct_id}SMStudio", 
                        vpc=vpc, 
                        bucket=bucket, 
                        api=api, 
                        ws_api_id=ws_api_id,
                        ws_api_endpoint=ws_api_endpoint,
                        ws_api_stage=ws_api_stage,
                        aws_region=aws_region, 
                        aws_account_id=aws_account_id,
                        db_writer_endpoint=db_writer_endpoint,
                        db_reader_endpoint=db_reader_endpoint,
                        db_secret_arn=db_secret_arn,
                        ssm_llm_parameter=ssm_llm_parameter,
                        ssm_recommendation_parameter=ssm_recommendation_parameter,
                        bastion_host_asg_name=bastion_host_asg_name
        )
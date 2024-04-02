#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

#
# This script copies the project's notebooks from S3 into the SageMaker Studio's EFS storage
#

set -eux


# Should already be running in user home directory, but just to check:
cd /home/sagemaker-user

# Generate JSON file to to refer to the deployed infrastructure
bucket_name="PLACEHOLDER_BUCKET_NAME"
db_writer_endpoint="PLACEHOLDER_DB_WRITER_ENDPOINT"
db_reader_endpoint="PLACEHOLDER_DB_READER_ENDPOINT"
api_url="PLACEHOLDER_API_URL"
ws_api_endpoint="PLACEHOLDER_WS_API_ENDPOINT"
ws_api_stage="PLACEHOLDER_WS_API_STAGE"
ssm_llm_parameter_name="PLACEHOLDER_SSM_LLM_PARAMETER_NAME"
ssm_recommendation_parameter_name="PLACEHOLDER_SSM_RECOMMENDATION_PARAMETER_NAME"
bastion_host_asg_name="PLACEHOLDER_BASTION_HOST_ASG_NAME"

cat > deployment-output.json << EOF
{
  "RecommenderStack": {
    "dbwriterendpoint": "${db_writer_endpoint}",
    "apiurl": "${api_url}",
    "wsapiendpoint": "${ws_api_endpoint}",
    "wsapistage": "${ws_api_stage}",
    "bucketname": "${bucket_name}",
    "dbreaderendpoint": "${db_reader_endpoint}",
    "ssmllmparametername": "${ssm_llm_parameter_name}",
    "ssmrecommendationparametername": "${ssm_recommendation_parameter_name}",
    "bastionhostasgname": "${bastion_host_asg_name}"
  }
}
EOF

# Copy notebook files from S3 into the EFS storage
aws s3 cp --recursive s3://${bucket_name}/notebooks/ .
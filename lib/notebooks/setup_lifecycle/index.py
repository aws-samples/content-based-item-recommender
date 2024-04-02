# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from typing import Any

import boto3
import base64

client = boto3.client("sagemaker")


def on_event(event, context):
    print(event)
    request_type = event["RequestType"].lower()
    if request_type == "create":
        return on_create(event)
    if request_type == "update":
        return on_update(event)
    if request_type == "delete":
        return on_delete(event)
    raise Exception(f"Invalid request type: {request_type}")


def encode_file(data):
    base64_bytes = base64.b64encode(data).decode("ascii")
    return base64_bytes


def on_create(event):
    props = event["ResourceProperties"]
    print(f"create new resource with {props=}")

    # Open the script and replace the placeholder values with the deployed infrastructure information
    data = open("scripts/bootstrap/on-jupyter-server-start.sh", "rb").read()
    data = data.replace(b"PLACEHOLDER_BUCKET_NAME", bytes(props['bucket_name'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_DB_WRITER_ENDPOINT", bytes(props['db_writer_endpoint'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_DB_READER_ENDPOINT", bytes(props['db_reader_endpoint'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_API_URL", bytes(props['api_url'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_WS_API_ENDPOINT", bytes(props['ws_api_endpoint'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_WS_API_STAGE", bytes(props['ws_api_stage'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_SSM_LLM_PARAMETER_NAME", bytes(props['ssm_llm_parameter_name'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_SSM_RECOMMENDATION_PARAMETER_NAME", bytes(props['ssm_recommendation_parameter_name'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_BASTION_HOST_ASG_NAME", bytes(props['bastion_host_asg_name'], "utf-8"))
    
    # Encode the file
    encoded_file_content = encode_file(data)

    # create the config
    response = client.create_studio_lifecycle_config(
        StudioLifecycleConfigName=f"{props['id_prefix']}-LCC",
        StudioLifecycleConfigContent=encoded_file_content,
        StudioLifecycleConfigAppType="JupyterLab",
    )
    physical_id = response["StudioLifecycleConfigArn"]

    # update existing user profiles
    return {"PhysicalResourceId": physical_id}


def on_update(event):
    props = event["ResourceProperties"]
    print(f"create new resource with {props=}")
    
    # Open the script and replace the placeholder values with the deployed infrastructure information
    data = open("scripts/bootstrap/on-jupyter-server-start.sh", "rb").read()
    data = data.replace(b"PLACEHOLDER_BUCKET_NAME", bytes(props['bucket_name'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_DB_WRITER_ENDPOINT", bytes(props['db_writer_endpoint'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_DB_READER_ENDPOINT", bytes(props['db_reader_endpoint'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_API_URL", bytes(props['api_url'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_WS_API_ENDPOINT", bytes(props['ws_api_endpoint'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_WS_API_STAGE", bytes(props['ws_api_stage'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_SSM_LLM_PARAMETER_NAME", bytes(props['ssm_llm_parameter_name'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_SSM_RECOMMENDATION_PARAMETER_NAME", bytes(props['ssm_recommendation_parameter_name'], "utf-8"))
    data = data.replace(b"PLACEHOLDER_BASTION_HOST_ASG_NAME", bytes(props['bastion_host_asg_name'], "utf-8"))
    
    # Encode the file
    encoded_file_content = encode_file(data)

    # delete the script
    client.delete_studio_lifecycle_config(
        StudioLifecycleConfigName=f"{props['id_prefix']}-LCC",
    )

    create_response = client.create_studio_lifecycle_config(
        StudioLifecycleConfigName=f"{props['id_prefix']}-LCC",
        StudioLifecycleConfigContent=encoded_file_content,
        StudioLifecycleConfigAppType="JupyterLab",
    )

    physical_id = create_response["StudioLifecycleConfigArn"]

    return {"PhysicalResourceId": physical_id}

def on_delete(event):
    props = event["ResourceProperties"]
    print(f"delete resource with {props=}")
    
    client.delete_studio_lifecycle_config(
        StudioLifecycleConfigName=f"{props['id_prefix']}-LCC",
    )

    return {"PhysicalResourceId": None}
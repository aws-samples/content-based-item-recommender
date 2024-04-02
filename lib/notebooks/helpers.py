# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Source: https://docs.aws.amazon.com/sagemaker/latest/dg/studio-jl.html
SAGEMAKER_IMAGE_REGION_ACCOUNT_MAPPING = {
    "us-east-1": "081325390199",
    "us-east-2": "429704687514",
    "us-west-1": "742091327244",
    "us-west-2": "236514542706",
    "af-south-1": "559312083959",
    "ap-east-1": "493642496378",
    "ap-south-1": "394103062818",
    "ap-northeast-2": "806072073708",
    "ap-southeast-1": "492261229750",
    "ap-southeast-2": "452832661640",
    "ap-northeast-1": "102112518831",
    "ca-central-1": "310906938811",
    "eu-central-1": "936697816551",
    "eu-west-1": "470317259841",
    "eu-west-2": "712779665605",
    "eu-west-3": "615547856133",
    "eu-north-1": "243637512696",
    "eu-south-1": "592751261982",
    "sa-east-1": "782484402741",
}


def get_sagemaker_image_arn(image_name, aws_region):
    account_id = SAGEMAKER_IMAGE_REGION_ACCOUNT_MAPPING[aws_region]
    return f"arn:aws:sagemaker:{aws_region}:{account_id}:image/{image_name}"
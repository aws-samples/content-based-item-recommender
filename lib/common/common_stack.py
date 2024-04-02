from constructs import Construct
from aws_cdk import (
    aws_ec2 as ec2,
    aws_apigateway as apigw,
    aws_s3 as s3,
    aws_iam as iam,
    aws_lambda as _lambda,
    Aws, Stack, NestedStack, RemovalPolicy, Duration, CustomResource
)
from aws_cdk.custom_resources import Provider

class CommonStack(NestedStack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        
        aws_region = Stack.of(self).region
        aws_account_id = Stack.of(self).account
        
        # S3 bucket
        bucket = s3.Bucket(self, f'Bucket',
           block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
           removal_policy=RemovalPolicy.DESTROY,
           #auto_delete_objects=True,
           server_access_logs_prefix="access_logs/",
           enforce_ssl=True
        )
        self.bucket = bucket
        
        # VPC
        vpc = ec2.Vpc(self, "Vpc",
          ip_addresses         = ec2.IpAddresses.cidr("10.120.0.0/16"),
          max_azs              = 3,
          enable_dns_support   = True,
          enable_dns_hostnames = True,
          nat_gateways=1,
          subnet_configuration = [
            ec2.SubnetConfiguration(
              cidr_mask   = 24,
              name        = 'public',
              subnet_type = ec2.SubnetType.PUBLIC,
            ),
            ec2.SubnetConfiguration(
              cidr_mask   = 24,
              name        = 'private_with_egress',
              subnet_type = ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            ec2.SubnetConfiguration(
              cidr_mask   = 24,
              name        = 'private',
              subnet_type = ec2.SubnetType.PRIVATE_ISOLATED,
            )
          ]
        )

        vpc.add_flow_log("FlowLogS3")
        
        self.vpc = vpc
        self.private_subnets =  ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)
        self.private_with_egress_subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
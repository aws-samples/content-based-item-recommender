from constructs import Construct
from aws_cdk import (
    aws_rds as rds,
    aws_ec2 as ec2,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_kms as kms,
    aws_sns as sns,
    aws_autoscaling as autoscaling,
    aws_secretsmanager as secretsmanager,
    Aws, NestedStack, Duration, BundlingOptions
)
from aws_cdk import CustomResource, RemovalPolicy
from aws_cdk.custom_resources import Provider
from cdk_nag import NagSuppressions
import json

class VectorDBStack(NestedStack):
    def __init__(self, 
                 scope: Construct, 
                 id: str, 
                 vpc, 
                 private_subnets, 
                 private_with_egress_subnets, 
                 embedding_dimension, 
                 database_name,
                 deploy_bastion_host,
                 **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        
        if deploy_bastion_host:

            # KMS key
            kms_key = kms.Key(self, "KMSKey",
              enable_key_rotation= True
            )
        
            # Also create the EC2 instance to access the private database via AWS SSM
            ssm_role = iam.Role(self, 'SSMEC2Role', 
                assumed_by=iam.ServicePrincipal('ec2.amazonaws.com'),
                inline_policies= 
                    {
                      "EC2SSMPolicy": iam.PolicyDocument(
                          statements=[
                              iam.PolicyStatement(
                                  actions=[
                                      "ssmmessages:CreateControlChannel",
                                      "ssmmessages:CreateDataChannel",
                                      "ssmmessages:OpenControlChannel",
                                      "ssmmessages:OpenDataChannel"
                                  ],
                                  resources=["*"],
                                  effect=iam.Effect.ALLOW,
                              ),
                              iam.PolicyStatement(
                                  actions=[
                                      "kms:Decrypt"
                                  ],
                                  resources=[kms_key.key_arn],
                                  effect=iam.Effect.ALLOW,
                              )
                          ]
                      )
                  },
            )

            # Suppres cdk_nag rule for * resource in IAM policy since ssmmessages actions need * in resource as per documentation
            # Documentation : https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonsessionmanagermessagegatewayservice.html
            NagSuppressions.add_resource_suppressions(ssm_role, [
              { "id": 'AwsSolutions-IAM5', "reason": 'ssmmessages actions need * resource' }
            ])

            bastion_host_security_group = ec2.SecurityGroup(self, 'BastionHostSecurityGroup',
              vpc=vpc,
              description='Security Group for bastion host',
              allow_all_outbound=True
            )

            ami = ec2.AmazonLinuxImage(
              generation= ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
              cpu_type=ec2.AmazonLinuxCpuType.ARM_64,
            )

            # The script to be run on the bastion host when launched
            bastion_host_user_data = ec2.UserData.for_linux()
            bastion_host_user_data.add_commands(
              "yum update -y",
              "yum upgrade -y",
              "amazon-linux-extras install postgresql12",
            )

            # The launch template for the bastion host
            bastion_host_launch_template = ec2.LaunchTemplate(self, "BastionHostLaunchTemplate",
              instance_type=ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.NANO),
              machine_image= ami,
              block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(volume_size=20, encrypted=True),
                )],
              role= ssm_role,
              security_group = bastion_host_security_group,
              detailed_monitoring=True,
              user_data=bastion_host_user_data 
            )

            # Bastion host auto scaling group for auto recovery, with desired instance number = 1
            bastion_asg = autoscaling.AutoScalingGroup(self, "BastionASG",
              auto_scaling_group_name="RecommendationBastionASG",
              launch_template=bastion_host_launch_template,
              vpc=vpc,
              vpc_subnets= private_with_egress_subnets,
              desired_capacity=1,
              notifications=[autoscaling.NotificationConfiguration(
                  topic= sns.Topic(self, "BastionASGEvents", topic_name="BastionASGEvents", master_key=kms_key)
              )]
            )
            
            self.bastion_host_asg_name = bastion_asg.auto_scaling_group_name
        
        # Subnet group
        db_subnet_group = rds.SubnetGroup(self, 'AuroraSubnetGroup',
           vpc = vpc,
           description = "Aurora subnet group",
           vpc_subnets = private_subnets,
           subnet_group_name= "Aurora subnet group"
        )
        
        # Creating security group to be used by Lambda function that rotates DB secret
        secret_rotation_security_group = ec2.SecurityGroup(self, 'RotateDBSecurityGroup',
            vpc = vpc,
            allow_all_outbound = True,
            description = "Security group for Lambda function that rotates DB secret",
            security_group_name = "SecretRotatorSecurityGroup",
        )

        # Creating the database security group
        db_security_group = ec2.SecurityGroup(self, 'VectorDBSecurityGroup',
            vpc = vpc,
            allow_all_outbound = True,
            description = "Security group for Aurora Serverless PostgreSQL",
            security_group_name = "AuroraServerlessSecurityGroup",
        )
        db_security_group.add_ingress_rule(
            peer =db_security_group,
            connection =ec2.Port(protocol=ec2.Protocol("ALL"), string_representation="ALL"),
            description="Any in connection from self"
        )
        
        if deploy_bastion_host:
            db_security_group.add_ingress_rule(
                peer = bastion_host_security_group,
                connection =ec2.Port(protocol=ec2.Protocol("TCP"), from_port=5432, to_port=5432, string_representation="tcp5432 PostgreSQL"),
                description="Postgresql port in from bastion host"
            )
        
        db_security_group.add_ingress_rule(
            peer =ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection =ec2.Port(protocol=ec2.Protocol("TCP"), from_port=5432, to_port=5432, string_representation="tcp5432 PostgreSQL"),
            description="Postgresql port in from within the VPC"
        )

        db_security_group.add_ingress_rule(
          peer= ec2.Peer.security_group_id(secret_rotation_security_group.security_group_id),
          connection=ec2.Port.tcp(5432),
          description="Allow DB access from Lambda Functions that rotate Secrets"
        )

        #db_security_group.add_egress_rule(
        #  peer =ec2.Peer.ipv4("0.0.0.0/0"),
        #  connection =ec2.Port(protocol=ec2.Protocol("ALL"), string_representation="ALL"),
        #  description="Any out connection"
        #)
        
        # Create secret for use with Aurora Serverless
        aurora_cluster_username="clusteradmin"
        aurora_cluster_secret = secretsmanager.Secret(self, "AuroraClusterCredentials",
          secret_name = "AuroraClusterCredentials",
          description = "Aurora Cluster Credentials",
          generate_secret_string=secretsmanager.SecretStringGenerator(
            exclude_characters ="\"@/\\ '",
            generate_string_key ="password",
            password_length =30,
            secret_string_template=json.dumps(
              {
                "username": aurora_cluster_username,
                "engine": "postgres"
              })),
        )
        aurora_cluster_secret.add_rotation_schedule(
          id="1",
          automatically_after=Duration.days(30),
          hosted_rotation=secretsmanager.HostedRotation.postgre_sql_single_user(
            security_groups=[secret_rotation_security_group],
            vpc=vpc,
            vpc_subnets=private_with_egress_subnets
          )
        )
        self.db_secret_arn = aurora_cluster_secret.secret_full_arn
        aurora_cluster_credentials = rds.Credentials.from_secret(aurora_cluster_secret, aurora_cluster_username)
        
        # Provisioning the Aurora Serverless database
        aurora_cluster = rds.DatabaseCluster(self, 'AuroraDatabase',
          credentials= aurora_cluster_credentials,
          engine= rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_15_3),
          writer=rds.ClusterInstance.serverless_v2("writer"),
          readers=[
            rds.ClusterInstance.serverless_v2("reader1",  scale_with_writer=True),
          ],
          serverless_v2_min_capacity=0.5,
          serverless_v2_max_capacity=1,
          default_database_name=database_name,
          security_groups=[db_security_group],
          vpc=vpc,
          subnet_group=db_subnet_group,
          storage_encrypted=True,
          iam_authentication=True,
          deletion_protection=True,
        )
        
        self.db_writer_endpoint = aurora_cluster.cluster_endpoint
        self.db_reader_endpoint = aurora_cluster.cluster_read_endpoint
        
        # Lambda for setting up database
        db_setup_event_handler = _lambda.Function(self, 'DatabaseSetupHandler',
            runtime=_lambda.Runtime.PYTHON_3_12,
            timeout=Duration.minutes(1),
            code=_lambda.Code.from_asset('./lib/vectordb/db_setup_lambda',
                bundling= BundlingOptions(
                  image= _lambda.Runtime.PYTHON_3_12.bundling_image,
                  command= [
                    'bash',
                    '-c',
                    'pip install -r requirements.txt -t /asset-output && cp -au . /asset-output',
                  ],
                )
            ),
            environment = {
                'DB_WRITER_ENDPOINT': self.db_writer_endpoint.hostname,
                'DATABASE_NAME': database_name,
                "EMBEDDING_DIMENSION": str(embedding_dimension)
            },
            vpc=vpc,
            vpc_subnets=private_with_egress_subnets,
            handler='index.on_event'
        )

        # Suppress CDK nag rule to allow the use of AWS managed policies/roles AWSLambdaVPCAccessExecutionRole and AWSLambdaBasicExecutionRole
        NagSuppressions.add_resource_suppressions(db_setup_event_handler, [
            { "id": 'AwsSolutions-IAM4', "reason": 'Allow the use of AWS managed policies/roles AWSLambdaVPCAccessExecutionRole and AWSLambdaBasicExecutionRole'},
        ], True)
        
        # IAM Policy statement for the Lambda function that configures the database
        statement = iam.PolicyStatement()
        statement.add_actions("secretsmanager:GetSecretValue")
        statement.add_resources(aurora_cluster_secret.secret_full_arn)
        db_setup_event_handler.add_to_role_policy(statement)
 
        provider = Provider(self, f'{id}DatabaseSetupProvider', 
                    on_event_handler=db_setup_event_handler)

        
        # Suppress cdk_nag rule for using not the latest runtime for non container Lambda, as this is managed by CDK Provider.
        # Also suppress it for using * in IAM policy and for using managed policy, as this is managed by CDK Provider.
        NagSuppressions.add_resource_suppressions(provider, [
            { "id": 'AwsSolutions-L1', "reason": "The Lambda function's runtime is managed by CDK Provider. Solution is to update CDK version."},
            { "id": 'AwsSolutions-IAM4', "reason": 'The Lambda function is managed by Provider'},
            { "id": 'AwsSolutions-IAM5', "reason": 'The Lambda function is managed by Provider.'}
        ], True)


        db_setup_custom_resource = CustomResource(
            scope=self,
            id='DatabaseSetup',
            service_token=provider.service_token,
            removal_policy=RemovalPolicy.DESTROY,
            resource_type="Custom::DatabaseSetupCustomResource"
        )

        db_setup_custom_resource.node.add_dependency(aurora_cluster)     

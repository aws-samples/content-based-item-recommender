# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import time
import boto3

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


def on_create(event):
    props = event["ResourceProperties"]
    print(f"create new resource with {props=}")
    
    space_name = f"{props['id_prefix']}Space"
    
    response = client.create_space(
        DomainId=props['domain_id'],
        SpaceName=space_name,
        SpaceDisplayName="Recommender",
        OwnershipSettings={ 
          "OwnerUserProfileName": props['user_profile_name']
        },
        SpaceSettings={
            "AppType": "JupyterLab",
            "JupyterLabAppSettings": { 
                 "DefaultResourceSpec": { 
                    "InstanceType": "ml.t3.medium",
                    "LifecycleConfigArn": props['lcc_arn'],
                    "SageMakerImageArn": props['image_arn']
                 }
            },
            "SpaceStorageSettings": { 
                "EbsStorageSettings": { 
                    "EbsVolumeSizeInGb": 30
                },
            }
        },
        SpaceSharingSettings={
            'SharingType': 'Private'
        }
    )
    space_arn = response["SpaceArn"]


    space = client.describe_space(
        DomainId=props['domain_id'],
        SpaceName=space_name
    )
    while space['Status'] != "InService":
        space = client.describe_space(
            DomainId=props['domain_id'],
            SpaceName=space_name
        )


    response = client.create_app(
        DomainId=props['domain_id'],
        SpaceName=space_name,
        AppType="JupyterLab",
        AppName="default",
        ResourceSpec={
            'SageMakerImageArn': props['image_arn'],
            'SageMakerImageVersionAlias': props['version_alias'],
            'InstanceType': "ml.t3.medium",
            'LifecycleConfigArn': props['lcc_arn']

        }
    )
    app_arn = response["AppArn"]

    physical_id = space_arn + app_arn
    print(physical_id)
    
    return {"PhysicalResourceId": physical_id}


def on_update(event):
    props = event["ResourceProperties"]
    print(f"no op")

    return {"PhysicalResourceId": physical_id}


def get_users(domain_id):
    response = client.list_user_profiles(DomainIdEquals=domain_id)
    users = response["UserProfiles"]
    while "NextToken" in response:
        response = client.list_user_profiles(
            DomainIdEquals=domain_id, NextToken=users["NextToken"]
        )
        users.extend(response["UserProfiles"])

    return users


def on_delete(event):
    props = event["ResourceProperties"]
    print(f"delete resource with {props=}")
    
    response = client.list_spaces(
        DomainIdEquals=props['domain_id']
    )

    spaces = response["Spaces"]
    while "NextToken" in response:
        response = client.list_spaces(
            DomainIdEquals=props['domain_id'], NextToken=users["NextToken"]
        )
        spaces.extend(response["Spaces"])

    print(spaces)

    for space in spaces:
        response = client.list_apps(
            DomainIdEquals=props['domain_id'],
            SpaceNameEquals=space['SpaceName']
        )
        apps = response["Apps"]
        while "NextToken" in response:
            response = client.list_apps(
                DomainIdEquals=props['domain_id'], SpaceNameEquals=space['SpaceName'], NextToken=users["NextToken"]
            )
            apps.extend(response["Apps"])

        for app in apps:
            try:
                response = client.delete_app(
                    DomainId=props['domain_id'],
                    AppType=app['AppType'],
                    AppName=app['AppName'],
                    SpaceName=app['SpaceName']
                )

                status = client.describe_app(
                    DomainId=props['domain_id'],
                    AppType=app['AppType'],
                    AppName=app['AppName'],
                    SpaceName=app['SpaceName']
                )['Status']
                while status != "Deleted" and status != "Failed":
                    time.sleep(2)
                    status = client.describe_app(
                        DomainId=props['domain_id'],
                        AppType=app['AppType'],
                        AppName=app['AppName'],
                        SpaceName=app['SpaceName']
                    )['Status']

            except Exception as e:
                print("Caught error below yet proceeding anyway")
                print(e)

        try:
            response = client.delete_space(DomainId=props['domain_id'], SpaceName=space['SpaceName'])

            status = client.describe_space(
                DomainId=props['domain_id'],
                SpaceName=app['SpaceName']
            )['Status']
            
            # This loop should also exit when the space is no longer found (successfully deleted) where it will go to the Exception block.
            while status != "Delete_Failed" and status != "Failed":
                time.sleep(2)
                status = client.describe_space(
                    DomainId=props['domain_id'],
                    SpaceName=app['SpaceName']
                )['Status']

        except Exception as e:
            print("Caught error below yet proceeding anyway")
            print(e)
    
    # List the user profiles. For each of the user profile, delete them and wait till deleted.
    response = client.list_user_profiles(
        DomainIdEquals=props['domain_id']
    )
    user_profiles = response["UserProfiles"]
    while "NextToken" in response:
        response = client.list_user_profiles(
            DomainIdEquals=props['domain_id'], NextToken=user_profiles["NextToken"]
        )
        user_profiles.extend(response["UserProfiles"])

    for user_profile in user_profiles:
        try:
            response = client.delete_user_profile(
                DomainId=props['domain_id'],
                UserProfileName=user_profile['UserProfileName']
            )

            status = client.describe_user_profile(
                DomainId=props['domain_id'],
                UserProfileName=user_profile['UserProfileName']
            )['Status']
            
            # This loop should also exit when the user profile is no longer found (successfully deleted) where it will go to the Exception block.
            while status != "Delete_Failed" and status != "Failed":
                time.sleep(2)
                status = client.describe_user_profile(
                    DomainId=props['domain_id'],
                    UserProfileName=user_profile['UserProfileName']
                )['Status']

        except Exception as e:
            print("Caught error below yet proceeding anyway")
            print(e)

    # Delete domain, wait until deleted
    try:
        client.delete_domain(
            DomainId=props['domain_id'],
            RetentionPolicy={ 
              "HomeEfsFileSystem": "Delete"
           }
        )
        status = client.describe_domain(
            DomainId=props['domain_id'],
        )['Status']
        
        # This loop should also exit when the domain is no longer found (successfully deleted) where it will go to the Exception block.
        while status != "Delete_Failed" and status != "Failed":
            time.sleep(2)
            status = client.describe_domain(
                DomainId=props['domain_id'],
            )['Status']
    except Exception as e:
        print("Caught error below yet proceeding anyway")              
        print(e)                  

    return {"PhysicalResourceId": None}


def update_user_profiles(domain_id, physical_id):
    users = get_users(domain_id)
    for user in users:
        client.update_user_profile(
            DomainId=domain_id,
            UserProfileName=user["UserProfileName"],
            UserSettings={
                "JupyterServerAppSettings": {
                    "DefaultResourceSpec": {"LifecycleConfigArn": physical_id},
                    "LifecycleConfigArns": [physical_id],
                }
            },
        )
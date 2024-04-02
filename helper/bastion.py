import boto3

autoscaling_client = boto3.client("autoscaling")

def find_instances(bastion_asg, next=None):
    if next:
        asg_instances = autoscaling_client.describe_auto_scaling_groups(NextToken=next)
    else:
        asg_instances = autoscaling_client.describe_auto_scaling_groups()
    
    for g in asg_instances['AutoScalingGroups']:
        if g['AutoScalingGroupName'] == bastion_asg: 
            return g['Instances'][0]['InstanceId']

    if 'NextToken' in asg_instances:
        return find_instances(bastion_asg, asg_instances['NextToken'])

    return None
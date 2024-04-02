#!/bin/bash

if [ ! -d "venv" ]; then
    virtualenv -p python3 venv
fi

source venv/bin/activate

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

nvm use 20.10.0 || nvm use 16.20.2
node -e "console.log('Currently running Node.js ' + process.version)"

account=$(aws sts get-caller-identity | jq -r '.Account')
export CDK_DEPLOY_ACCOUNT=$account

default_region="us-west-2" # This is a default value which is expected to be overridden by user input.
if [ -f .DEFAULT_REGION ]
then
    default_region=$(cat .DEFAULT_REGION)
fi
echo ""
read -p "Which AWS region the solution you want to destroy currently is deployed on? (default: $default_region) : " region
region=${region:-$default_region} 
export CDK_DEPLOY_REGION=$region

bastion="placeholder"
environment="placeholder"

cdk destroy --context deployBastionHost=$bastion --context environment=$environment

deactivate
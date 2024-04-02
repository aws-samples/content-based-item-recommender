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
read -p "Which AWS region to deploy this solution to? (default: $default_region) : " region
region=${region:-$default_region} 
export CDK_DEPLOY_REGION=$region

default_bastion=true # This is a default value
read -p "Do you want to deploy a bastion host too? (valid: true/false) (default: true) :" bastion
bastion=${bastion:-$default_bastion} 

default_environment="dev" # This is a default value
read -p "What is the environment name? (default: dev) :" environment
environment=${environment:-$default_environment} 

zip -r notebooks.zip 01-load-data.ipynb 02-prompt-building-and-inference-test.ipynb 03-deploy-templates-and-parameters.ipynb 04-inference-and-data-loading-with-api.ipynb LICENSE CODE_OF_CONDUCT.md CONTRIBUTING.md README.md helper data assets

zip prompt_template.txt.zip prompt_template.txt
zip vector_search_query.txt.zip vector_search_query.txt
zip vector_insert_query.txt.zip vector_insert_query.txt

cdk deploy --outputs-file ./deployment-output.json --context deployBastionHost=$bastion --context environment=$environment

deactivate
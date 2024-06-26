#!/bin/bash

if [ ! -d "venv" ]; then
    virtualenv -p python3 venv
fi

source venv/bin/activate

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

nvm use 20.0.0 || nvm use 16.0.0
node -e "console.log('Currently running Node.js ' + process.version)"

account=$(aws sts get-caller-identity | jq -r '.Account')
export CDK_DEPLOY_ACCOUNT=$account

bastion=true # This is a default value
echo "Bootstrap is using default value for bastion with value = true"
environment="dev" # This is a default value
echo "Bootstrap is using default value for environment with value = dev"

default_region="us-west-2" # This is a default value which is expected to be overridden by user input.
echo ""
read -p "Which AWS region to deploy this solution to? (default: $default_region) : " region
region=${region:-$default_region} 
export CDK_DEPLOY_REGION=$region
echo $region > .DEFAULT_REGION

zip -r notebooks.zip 01-load-data.ipynb 02-prompt-building-and-inference-test.ipynb 03-deploy-templates-and-parameters.ipynb 04-inference-and-data-loading-with-api.ipynb LICENSE CODE_OF_CONDUCT.md CONTRIBUTING.md README.md helper data assets

zip prompt_template.txt.zip prompt_template.txt
zip vector_search_query.txt.zip vector_search_query.txt
zip vector_insert_query.txt.zip vector_insert_query.txt

cdk bootstrap aws://${account}/${region} --context deployBastionHost=$bastion --context environment=$environment

deactivate
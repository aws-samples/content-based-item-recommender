#!/bin/bash

python3 -m pip install bandit
bandit -r . -o banditreport.txt -f txt --exclude "./cdk.out/,./venv/"

python3 -m pip install semgrep
semgrep login
semgrep scan --exclude "./cdk.out/" -o semgrepreport.txt &> semgreplog.txt
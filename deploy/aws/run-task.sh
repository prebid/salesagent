#!/usr/bin/env bash
# Run a one-off task from this deployment's task definitions, wait for it, print its log.
#
#   deploy/aws/run-task.sh db-admin                      # create the databases and roles
#   deploy/aws/run-task.sh app python scripts/setup/setup_tenant.py "Acme" --virtual-host acme.example.com
#
# Reads the Terraform outputs from $TF_OUTPUTS (default outputs.json), written with
# `terraform output -json > outputs.json`. Needs the AWS CLI and jq.
set -euo pipefail

which="${1:?usage: run-task.sh <app|db-admin> [command ...]}"
shift
out="${TF_OUTPUTS:-outputs.json}"
tf() { jq -r "$1" "$out"; }

region=$(tf .region.value)
cluster=$(tf .cluster.value)
case "$which" in
  app) family=$(tf .task_definitions.value.app_task); net=$(tf .run_task_network_configuration.value) ;;
  db-admin) family=$(tf .task_definitions.value.db_admin); net=$(tf .db_admin_network_configuration.value) ;;
  *) echo "unknown task: $which" >&2; exit 2 ;;
esac

args=(--region "$region" --cluster "$cluster" --task-definition "$family"
      --launch-type FARGATE --network-configuration "$net")
if [ $# -gt 0 ]; then
  # NUL-separated through stdin: jq would read a leading "--" in an argument as its own option.
  command_json=$(printf '%s\0' "$@" | jq -Rsc 'split("\u0000")[:-1]')
  args+=(--overrides "$(jq -cn --arg name "$which" --argjson cmd "$command_json" '{containerOverrides: [{name: $name, command: $cmd}]}')")
fi

task_arn=$(aws ecs run-task "${args[@]}" --query 'tasks[0].taskArn' --output text)
task_id="${task_arn##*/}"
echo "task $task_id started; waiting for it to stop" >&2
aws ecs wait tasks-stopped --region "$region" --cluster "$cluster" --tasks "$task_arn"

aws logs get-log-events --region "$region" --start-from-head \
  --log-group-name "$(tf ".log_groups.value[\"$which\"]")" \
  --log-stream-name "$which/$which/$task_id" \
  --query 'events[].message' --output text | tr '\t' '\n'

exit_code=$(aws ecs describe-tasks --region "$region" --cluster "$cluster" --tasks "$task_arn" \
  --query 'tasks[0].containers[0].exitCode' --output text)
echo "exit code: $exit_code" >&2
[ "$exit_code" = "0" ]

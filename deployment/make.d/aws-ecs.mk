##
# AWS ECS
#

DPL_ECS_IMAGE = $(DPL_ECR_URI)/$(DPL_ECR_REPO)

DPL_ECS_TASK = $(or $(call dpl_get_var,task),$(DPL_NAME))
DPL_ECS_CLUSTER = $(call dpl_get_var,cluster,$(DPL_NAME))
DPL_ECS_SERVICE = $(call dpl_get_var,service,$(DPL_NAME))

DPL_ECS_TASK_FILE = $(or $(wildcard $(DPL_DIR)/ecs/task-defs/$(DPL_ECS_TASK).json),$(if $(call eq,$(DPL_ECS_TASK),$(DPL_NAME)),$(wildcard $(DPL_DIR)/ecs/task-defs/default.json)))

# shell command to get the task definition family name:
DPL_ECS_TASK_FAMILY_SH = $(or $(and $(DPL_ECS_TASK_FILE),$(DPL_ECS_TASK_DEF_LOCAL_SH) | jq -r '.family'),echo $(DPL_ECS_TASK))

# shell command to get the updated task definition:
DPL_ECS_TASK_DEF_SH=$(or $(and $(DPL_ECS_TASK_FILE),$(DPL_ECS_TASK_DEF_LOCAL_SH)),$(DPL_ECS_TASK_DEF_CLOUD_SH))

# shell command to get the updated task definition from a file:
DPL_ECS_TASK_DEF_LOCAL_SH=cat $(DPL_ECS_TASK_FILE) | $(DPL_ENV_VARS) envsubst

# shell command to get the updated task definition from its cloud version:
DPL_ECS_TASK_DEF_CLOUD_SH=aws ecs describe-task-definition --task-definition $$($(DPL_ECS_TASK_FAMILY_SH)) --region $(DPL_AWS_REGION) --no-cli-pager --output json | jq --arg IMAGE "$(DPL_DOCKER_IMAGE)" '.taskDefinition | .containerDefinitions[0].image = $$IMAGE | del(.taskDefinitionArn) | del(.revision) | del(.status) | del(.requiresAttributes) | del(.compatibilities) | del(.registeredAt) | del(.registeredBy)'

_dpl-ECS-debug: _dpl-ECR-debug
	@echo; echo "* aws-ecs.mk"; echo
	@echo DPL_ECS_IMAGE=$(DPL_ECS_IMAGE)
	@echo DPL_ECS_CLUSTER=$(DPL_ECS_CLUSTER)
	@echo DPL_ECS_SERVICE=$(DPL_ECS_SERVICE)
	@echo DPL_ECS_TASK=$(DPL_ECS_TASK)
	@echo DPL_ECS_TASK_FILE=$(DPL_ECS_TASK_FILE)
	@echo DPL_ECS_TASK_FAMILY=$$($(DPL_ECS_TASK_FAMILY_SH))

# help:
_aws-ecs-help:
	@$(if $(inc),true,echo "Usage: make <target>")
	@$(if $(inc),true,echo)
	@echo "  - ecs-update-task env=<env> [task=...]"
	@echo "    Update task definition."
	@echo
	@echo "  - ecs-restart-service env=<env> [cluster=...] [service=...]"
	@echo "    Restart ECS service."
	@echo

# update task definition:
ecs-update-task:
	TASK_DEF=$$($(DPL_ECS_TASK_DEF_SH)); \
	aws ecs register-task-definition --region $(DPL_AWS_REGION) --cli-input-json "$$TASK_DEF" --no-cli-pager

# restart ECS service:
ecs-restart-service:
	$(or $(and $(DPL_ECS_CLUSTER),$(DPL_ECS_SERVICE),aws ecs update-service --region $(DPL_AWS_REGION) --cluster $(DPL_ECS_CLUSTER) --service $(DPL_ECS_SERVICE) --task-definition $$($(DPL_ECS_TASK_FAMILY_SH)) --force-new-deployment --no-cli-pager),true)

_dpl-ECS-docker-build: _dpl-ECR-docker-build

_dpl-ECS-docker-push: _dpl-ECR-docker-push

_dpl-ECS: docker-build _dpl-ECS-docker-push ecs-update-task ecs-restart-service

_dpl-ECS-render:
	@TASK_DEF=$$($(DPL_ECS_TASK_DEF_SH)); \
	echo $$TASK_DEF | jq

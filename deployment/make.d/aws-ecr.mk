##
# AWS ECR
#

DPL_ECR_REPO = $(or $(call dpl_get_var,ecr_repo),$(DPL_NAME))
DPL_ECR_URI = $(DPL_AWS_ACCOUNT_ID).dkr.ecr.$(DPL_AWS_REGION).amazonaws.com

_dpl-ECR-debug: _docker-debug _dpl-AWS-debug
	@echo; echo "* aws-ecr.mk"; echo
	@echo DPL_ECR_REPO=$(DPL_ECR_REPO)
	@echo DPL_ECR_URI=$(DPL_ECR_URI)

_dpl-ECR-login:
	aws ecr get-login-password --region $(DPL_AWS_REGION) | docker login --username AWS --password-stdin $(DPL_ECR_URI)

_dpl-ECR-docker-build: _dpl-docker-build-default

# push Docker image to Elastic Container Registry:
_dpl-ECR-docker-push: docker-build _dpl-ECR-login
	docker push $(DPL_DOCKER_IMAGE)
	docker push $(DPL_DOCKER_NAME):latest$(DPL_DOCKER_TAG_SUFFIX)

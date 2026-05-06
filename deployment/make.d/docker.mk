##
# Docker
#

DPL_DOCKER_CONTEXT=$(or $(call dpl_get_var,docker_context),$(DPL_APP_DIR))
DPL_DOCKER_FILE=$(or $(call dpl_get_var,docker_file),Dockerfile)

DPL_DOCKER_NAME = $(call DPL_$(DPL_PLATFORM)_IMAGE)
DPL_DOCKER_TAG = $(or $(CI_COMMIT_SHORT_SHA),$(shell if [ ! -z "$$(git rev-parse --is-inside-work-tree 2>/dev/null)" ]; then echo $$(git describe --exact-match --tags 2>/dev/null || git rev-parse --short HEAD)$$(git diff --quiet && git diff --cached --quiet && echo || echo -tainted); else echo tainted; fi))
DPL_DOCKER_TAG_SUFFIX=$(call dpl_get_var,docker_tag_suffix,-$(DPL_ENV_NAME))

DPL_DOCKER_IMAGE = $(or $(image),$(DPL_DOCKER_NAME):$(DPL_DOCKER_TAG)$(DPL_DOCKER_TAG_SUFFIX))

DPL_DOCKER_ENV_VARS = image="$(DPL_DOCKER_IMAGE)"

_docker-debug:
	@echo; echo "* docker.mk"; echo
	@echo DPL_DOCKER_CONTEXT=$(DPL_DOCKER_CONTEXT)
	@echo DPL_DOCKER_FILE=$(DPL_DOCKER_FILE)
	@echo DPL_DOCKER_NAME=$(DPL_DOCKER_NAME)
	@echo DPL_DOCKER_TAG=$(DPL_DOCKER_TAG)
	@echo DPL_DOCKER_TAG_SUFFIX=$(DPL_DOCKER_TAG_SUFFIX)
	@echo DPL_DOCKER_IMAGE=$(DPL_DOCKER_IMAGE)

# help:
_docker-help:
	@$(if $(inc),true,echo "Usage: make <target>")
	@$(if $(inc),true,echo)
	@echo "  - docker-build env=<env>"
	@echo "    Build Docker image."
	@echo
	@echo "  - docker-push env=<env>"
	@echo "    Push Docker image."
	@echo

# build Docker image:
docker-build: _dpl-check-env
	@$(if $(call dpl_has_target,docker-build-before),$(MAKE) docker-build-before $(DPL_CLI_ARGS),true)
	@$(MAKE) $(if $(call dpl_has_target,docker-build-custom),docker-build-custom,docker-build-default) $(DPL_CLI_ARGS)
	@$(if $(call dpl_has_target,docker-build-after),$(MAKE) docker-build-after $(DPL_CLI_ARGS),true)

docker-build-default:
	@$(MAKE) $(if $(call dpl_has_target,_dpl-$(DPL_PLATFORM)-docker-build),_dpl-$(DPL_PLATFORM)-docker-build,_dpl-docker-build-default) $(DPL_CLI_ARGS)

_dpl-docker-build-default:
	docker build -f $(DPL_DOCKER_FILE) \
		--build-arg ENV="$(DPL_ENV)" --build-arg ENV_NAME="$(DPL_ENV_NAME)" \
		--build-arg TAG="$(DPL_DOCKER_TAG)" \
		-t $(DPL_DOCKER_IMAGE) -t $(DPL_DOCKER_NAME):latest$(DPL_DOCKER_TAG_SUFFIX) \
		$(DPL_DOCKER_CONTEXT)

# push Docker image:
docker-push: _dpl-check-env
	@$(if $(call dpl_has_target,_dpl-$(DPL_PLATFORM)-docker-push),$(MAKE) _dpl-$(DPL_PLATFORM)-docker-push $(DPL_CLI_ARGS),$(error Error - docker-push on platform "$(DPL_PLATFORM)" is not implemented))

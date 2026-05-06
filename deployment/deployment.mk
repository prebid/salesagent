##
# Configuration

# location of the deployment dir, containing the deployment configuration and
# other assets:
ifndef DPL_DIR
	DPL_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
endif

# app dir:
ifndef DPL_APP_DIR
	DPL_APP_DIR := .
endif

# config file:
ifndef DPL_CONFIG_FILE
	DPL_CONFIG_FILE := $(DPL_DIR)/config.json
endif

##
# Variables

DPL_DATETIME := $(or $(datetime),$(shell date -u +"%Y%m%d%H%M%S"))

DPL_ENVS = $(shell cat $(DPL_CONFIG_FILE) | jq -r '.environments | keys_unsorted | join(", ")')
DPL_ENV = $(or $(env),$(error Error - you must specify an environment))

DPL_PLATFORM = $(call dpl_get_var,platform)
DPL_ENV_NAME = $(or $(call dpl_get_var,env_name),$(DPL_ENV))
DPL_NAME_PREFIX = $(call dpl_get_var,name_prefix)
DPL_NAME_SUFFIX = $(call dpl_get_var,name_suffix)
DPL_NAME = $(or $(call dpl_get_var,name),$(DPL_NAME_PREFIX)$(DPL_ENV_NAME)$(DPL_NAME_SUFFIX))

# all installed modules:
DPL_MODULES = $(shell echo $(notdir $(basename $(wildcard $(DPL_DIR)/make.d/*.mk))) | tr a-z A-Z | tr '-' '_')

# extract all variable names defined in config which are applicable to the
# environment:
DPL_CONFIG_VAR_NAMES = $(call dpl_read_vars,. | with_entries(select(.key != "environments"))) \
											 $(call dpl_read_vars,.environments."$(DPL_ENV)")

# create a list with all environment variables which can be used in shell
# commands:
DPL_ENV_VARS = DOLLAR='$$' \
							 datetime="$(DPL_DATETIME)" \
							 name="$(DPL_NAME)" \
							 env_name="$(DPL_ENV_NAME)" \
							 $(strip $(foreach n,$(DPL_MODULES),$(DPL_$(n)_ENV_VARS))) \
							 $(foreach n,$(call dpl_uniq,$(DPL_CONFIG_VAR_NAMES)),$n="$(call dpl_get_var,$n)")

# list with all CLI arguments passed when calling sub make commands:
DPL_CLI_ARGS = datetime="$(DPL_DATETIME)" \
							 $(strip $(foreach n,$(DPL_MODULES),$(DPL_$(n)_CLI_ARGS)))

##
# Macros

# get the value of a variable:
# $1 - variable's name
# $2 - optional default value in case variable is undefined
define dpl_get_var
$(or $($1),$(shell cat $(DPL_CONFIG_FILE) | jq -r '.environments."$(DPL_ENV)".$1 // .$1 // "$2"'))
endef

# extract all variables from the config file, given a starting JQ selector:
# $1 - JQ selector
define dpl_read_vars
$(shell cat $(DPL_CONFIG_FILE) | jq -r '$1 | keys | join(" ")')
endef

# check if an environment is defined in configuration:
# $1 - environment name
define dpl_check_env
$(or $(shell cat $(DPL_CONFIG_FILE) | jq -r '.environments | has("$1") // empty'), $(error Error - "$1" is not a configured environment))
endef

# get unique items from a list:
dpl_uniq = $(if $1,$(firstword $1) $(call dpl_uniq,$(filter-out $(firstword $1),$1)))

# get defined targets, optionally excluding hidden ones:
# $1 - exclude hidden
define dpl_get_targets
$(shell $(MAKE) -pRrq : 2>/dev/null | awk -v RS= -F: '/^# Implicit Rules/,/^# Finished Make data base/ {if ($$1 !~ "^[#.]") {print $$1}}' | sort | egrep -v $(and $1,-e '^[^[:alnum:]]') -e '^$@$$')
endef

# check if a target exists:
# $1 - target
define dpl_has_target
$(filter $1,$(call dpl_get_targets))
endef

# string equality:
# usage: $(if $(call eq,str1,str2),true,else)
define eq
$(and $(findstring $1,$2),$(findstring $2,$1))
endef

##
# Targets

DPL_HELP_TARGETS = $(filter-out deployment-help,$(filter %-help,$(call dpl_get_targets)))

deployment-help:
	@$(if $(inc),true,echo "Usage: make <target>")
	@echo
	@echo "Available targets:"
	@echo
	@echo "  - deploy env=<env>"
	@echo "    Deploy to an environment."
	@echo
	@echo "  - render env=<env>"
	@echo "    Render deployment configuration for an environment."
	@echo
	@for t in $(DPL_HELP_TARGETS); do $(MAKE) $$t inc=1; done
	@echo "Available environments: $(DPL_ENVS)"
	@echo

_debug: _dpl-check-env
	@echo; echo "* deployment.mk"; echo
	@echo "DPL_DIR=$(DPL_DIR)"
	@echo "DPL_APP_DIR=$(DPL_APP_DIR)"
	@echo "DPL_CONFIG_FILE=$(DPL_CONFIG_FILE)"
	@echo "DPL_DATETIME=$(DPL_DATETIME)"
	@echo "DPL_PLATFORM=$(DPL_PLATFORM)"
	@echo "DPL_NAME_PREFIX=$(DPL_NAME_PREFIX)"
	@echo "DPL_NAME_SUFFIX=$(DPL_NAME_SUFFIX)"
	@echo "DPL_NAME=$(DPL_NAME)"
	@echo "DPL_CONFIG_VAR_NAMES=$(DPL_CONFIG_VAR_NAMES)"
	@echo "DPL_ENVS=$(DPL_ENVS)"
	@echo "DPL_ENV=$(DPL_ENV)"
	@echo "DPL_ENV_VARS=$(DPL_ENV_VARS)"
	@$(if $(call dpl_has_target,update-env),$(MAKE) _env-debug $(DPL_CLI_ARGS),true)
	@$(if $(call dpl_has_target,_dpl-$(DPL_PLATFORM)-debug),$(MAKE) _dpl-$(DPL_PLATFORM)-debug $(DPL_CLI_ARGS),$(error Error - debug on platform "$(DPL_PLATFORM)" is not implemented))
	@echo

DEPLOYMENT.md: $(DPL_DIR)/deployment.md $(wildcard $(DPL_DIR)/make.d/*.md)
	@cat $^ > $@

deploy: _dpl-check-env
	@$(if $(call dpl_has_target,update-env),$(MAKE) update-env $(DPL_CLI_ARGS),true)
	@$(if $(call dpl_has_target,_dpl-$(DPL_PLATFORM)),$(MAKE) _dpl-$(DPL_PLATFORM) $(DPL_CLI_ARGS),$(error Error - deployment on platform "$(DPL_PLATFORM)" is not implemented))

render: _dpl-check-env
	@$(if $(call dpl_has_target,_dpl-$(DPL_PLATFORM)-render),$(MAKE) _dpl-$(DPL_PLATFORM)-render $(DPL_CLI_ARGS),$(error Error - render on platform "$(DPL_PLATFORM)" is not implemented))

_dpl-check-env:
	@$(call dpl_check_env,$(DPL_ENV))

##
# make.d

include $(wildcard $(DPL_DIR)/make.d/*.mk)

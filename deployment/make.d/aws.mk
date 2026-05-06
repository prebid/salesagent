##
# AWS
#

DPL_AWS_ACCOUNT_ID = $(or $(call dpl_get_var,account_id),$(call dpl_get_var,AWS_ACCOUNT_ID))
DPL_AWS_REGION = $(or $(call dpl_get_var,region),$(call dpl_get_var,AWS_REGION))

_dpl-AWS-debug:
	@echo; echo "* aws.mk"; echo
	@echo DPL_AWS_ACCOUNT_ID=$(DPL_AWS_ACCOUNT_ID)
	@echo DPL_AWS_REGION=$(DPL_AWS_REGION)


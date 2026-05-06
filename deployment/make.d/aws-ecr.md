### AWS ECR (`aws-ecr.mk`)

This module builds and pushes a Docker image to AWS ECR.

Required modules: `aws.mk`, `docker.mk`.

#### Config Variables

| Variable | Required | Default | Info |
| :--- | :--- | :--- | :--- |
| ecr\_repo | Yes | `{name}` | The ECR repository name. |

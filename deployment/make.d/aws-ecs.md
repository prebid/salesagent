### AWS ECS (`aws-ecs.mk`)

This module deploys to AWS ECS.

Required modules: `aws.mk`, `aws-ecr.mk`.

#### Minimal Config

```json
{
  "platform": "ECS"
}
```

#### Config Variables

| Variable | Required | Default | Info |
| :--- | :--- | :--- | :--- |
| image<sup>\*</sup> | Yes | `{account_id}.dkr.ecr.{region}.amazonaws.com/{ecr_repo}` | The Docker image name. |
| task | Yes | `{name}` | The ECS task definition name. |
| task\_file | No | `default` | The ECS task definition file template, which must be located in `{DPL_DIR}/ecs/task-defs/{task_file}.json`. |
| cluster | No | `{name}` | The ECS cluster name. |
| service | No | `{name}` | The ECS service name. |

<sup>\* Variable belongs to another module, but this module affects its value.</sup>

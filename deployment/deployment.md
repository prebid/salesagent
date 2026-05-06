## Deploy

```bash
make deploy env=<env>
```

## Help

```bash
make deployment-help
```

## Available Modules

### Core (`deployment.mk`)

#### Make Variables

| Variable | Default | Info |
| :--- | :--- | :--- |
| DPL\_DIR | Directory of `deployment.mk` | Should point to the directory where `config.json` and the other deployment assets can be found. |
| DPL\_APP\_DIR | `.` | Should point to the app's root directory. Defaults to the directory where the top Makefile is located. |

#### Config Variables

| Variable | Required | Default | Info |
| :--- | :--- | :--- | :--- |
| env | Yes | | The environment key. |
| env\_name | Yes | `{env}` | |
| name | Yes | `{name_prefix}{env_name}{name_suffix}` | |
| name\_prefix | No | | |
| name\_suffix | No | | |

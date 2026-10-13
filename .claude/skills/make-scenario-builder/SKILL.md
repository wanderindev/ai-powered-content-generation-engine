---
name: make-scenario-builder
description: Use this skill to build Make.com automation scenarios using the MCP integration. Includes blueprint structure, module configuration, and validation workflow.
---

# Make.com Scenario Builder

This skill provides guidance for programmatically building Make.com automation scenarios using the MCP (Model Context Protocol) integration.

## Prerequisites

- MCP connection to Make.com must be active
- User must have a Make.com account with API access
- Know the team ID (use `mcp__make__organizations_list` then `mcp__make__teams_list`)

## Blueprint JSON Structure

### Core Blueprint

```json
{
  "name": "Scenario Name",
  "flow": [
    // Array of modules (see Module Structure below)
  ],
  "metadata": {
    "version": 1,
    "scenario": {
      "roundtrips": 1,
      "maxErrors": 3,
      "autoCommit": true,
      "sequential": false,
      "confidential": false,
      "dataloss": false,
      "dlq": false
    },
    "designer": {
      "orphans": []
    }
  }
}
```

### Module Structure

Each module in the `flow` array:

```json
{
  "id": 1,
  "module": "app-name:moduleName",
  "version": 1,
  "parameters": {
    "__IMTCONN__": 12345
  },
  "mapper": {
    "field1": "static value",
    "field2": "{{1.outputField}}"
  },
  "metadata": {
    "designer": {
      "x": 0,
      "y": 150
    },
    "expect": []
  }
}
```

**Key properties:**
- `id`: Unique integer, sequential (1, 2, 3...)
- `module`: Format is `appName:moduleName` (e.g., `notion:createDataSourceItem`)
- `version`: Module version (usually 1 or 2)
- `parameters`: Static config - connection IDs, webhook IDs (NO IML expressions)
- `mapper`: Dynamic config - field values, CAN use IML expressions like `{{1.field}}`
- `metadata.designer`: Position in visual editor (x, y coordinates)

### Router Structure (Branching)

```json
{
  "id": 3,
  "module": "builtin:BasicRouter",
  "version": 1,
  "mapper": null,
  "metadata": {
    "designer": {
      "x": 300,
      "y": 0
    }
  },
  "routes": [
    {
      "flow": [
        // Nested modules for this branch
      ],
      "filter": {
        "name": "Branch Name",
        "conditions": [
          [
            {
              "a": "{{2.status}}",
              "o": "equal",
              "b": "active"
            }
          ]
        ]
      }
    },
    {
      "flow": [
        // Second branch modules
      ],
      "filter": {
        "name": "Fallback",
        "conditions": []
      }
    }
  ]
}
```

**Filter operators (`o`):**
- `equal`, `notequal`
- `less`, `lessequal`, `greater`, `greaterequal`
- `contain`, `notcontain`
- `startswith`, `endswith`
- `exist`, `notexist`
- `text:equal`, `text:notequal` (case-insensitive)

### Scheduling Structure

```json
{
  "type": "indefinitely",
  "interval": 15
}
```

**Schedule types:**
- `immediately` - Run once on activation
- `indefinitely` - Run at interval continuously
- `once` - Run once at specific time
- `daily` - Daily at specific time
- `weekly` - Weekly on specific days
- `monthly` - Monthly on specific date
- `yearly` - Yearly on specific date

**Common scheduling examples:**

```json
// Every 15 minutes
{"type": "indefinitely", "interval": 15}

// Once immediately
{"type": "immediately"}

// Daily at 9 AM
{
  "type": "daily",
  "time": "09:00:00",
  "days": [1, 2, 3, 4, 5]
}
```

## IML (Integromat Markup Language)

IML expressions reference data from previous modules:

| Expression | Description |
|------------|-------------|
| `{{1.field}}` | Field from module ID 1 |
| `{{1.data.nested}}` | Nested field access |
| `{{1.array[0]}}` | Array element |
| `{{now}}` | Current timestamp |
| `{{formatDate(1.date; "YYYY-MM-DD")}}` | Format date |
| `{{if(1.status = "active"; "yes"; "no")}}` | Conditional |
| `{{lower(1.name)}}` | Lowercase string |
| `{{parseJSON(1.body)}}` | Parse JSON string |

## Step-by-Step Workflow

### Step 1: Gather Requirements

Identify:
- What triggers the scenario? (webhook, schedule, app event)
- What apps are involved?
- What data flows between modules?
- Any conditional logic (routers)?

### Step 2: Find Apps and Connections

```
1. Use mcp__make__apps_recommend to find relevant apps
2. Use mcp__make__connections_list to get available connection IDs
3. Note the connection IDs needed for each app
```

### Step 3: Discover Modules

```
1. Use mcp__make__app-modules_list to list available modules
2. Identify trigger modules (watchXXX, webhooks)
3. Identify action modules (create, update, get, etc.)
```

### Step 4: Get Module Configuration

```
1. Use mcp__make__app-module_get with format="instructions"
2. This returns JSON schema for parameters and mapper
3. Note required fields vs optional
4. Note which fields go in parameters vs mapper
```

### Step 5: Build Module Configuration

For each module:
1. Set `parameters` with static values (connection IDs)
2. Set `mapper` with field values (can include IML)
3. Set proper `id` sequence

### Step 6: Validate Configuration

```
1. Use mcp__make__validate_module_configuration for each module
2. Use mcp__make__validate_blueprint_schema for complete blueprint
3. Use mcp__make__validate_scheduling_schema for scheduling
4. Fix any validation errors
```

### Step 7: Create the Scenario

```
Use mcp__make__scenarios_create with:
- teamId: Your team ID
- blueprint: The complete blueprint object
- scheduling: Scheduling configuration
- folderId: (optional) Folder to place scenario in
```

### Step 8: Activate (if needed)

```
Use mcp__make__scenarios_activate with the scenario ID
```

## Common Module Patterns

### Webhook Trigger

```json
{
  "id": 1,
  "module": "gateway:CustomWebHook",
  "version": 1,
  "parameters": {
    "hook": 123456
  },
  "mapper": {},
  "metadata": {
    "designer": {"x": 0, "y": 0}
  }
}
```

### HTTP Request

```json
{
  "id": 2,
  "module": "http:ActionSendData",
  "version": 3,
  "parameters": {},
  "mapper": {
    "url": "https://api.example.com/data",
    "method": "post",
    "headers": [
      {"name": "Content-Type", "value": "application/json"},
      {"name": "Authorization", "value": "Bearer {{1.token}}"}
    ],
    "body": "{{1.payload}}",
    "parseResponse": true
  },
  "metadata": {
    "designer": {"x": 300, "y": 0}
  }
}
```

### Google Sheets - Add Row

```json
{
  "id": 2,
  "module": "google-sheets:addRow",
  "version": 2,
  "parameters": {
    "__IMTCONN__": 789012
  },
  "mapper": {
    "spreadsheetId": "1abc...",
    "sheetId": "Sheet1",
    "values": {
      "A": "{{1.name}}",
      "B": "{{1.email}}",
      "C": "{{now}}"
    }
  },
  "metadata": {
    "designer": {"x": 300, "y": 0}
  }
}
```

### Slack - Send Message

```json
{
  "id": 3,
  "module": "slack:createAMessage",
  "version": 1,
  "parameters": {
    "__IMTCONN__": 345678
  },
  "mapper": {
    "channel": "#notifications",
    "text": "New submission from {{1.name}}: {{1.message}}"
  },
  "metadata": {
    "designer": {"x": 600, "y": 0}
  }
}
```

### JSON Parse

```json
{
  "id": 2,
  "module": "json:ParseJSON",
  "version": 1,
  "parameters": {},
  "mapper": {
    "json": "{{1.body}}"
  },
  "metadata": {
    "designer": {"x": 300, "y": 0}
  }
}
```

## Error Handling

Add `onerror` to modules for error handling:

```json
{
  "id": 2,
  "module": "http:ActionSendData",
  "version": 3,
  "onerror": [
    {
      "id": 3,
      "module": "builtin:Break",
      "version": 1,
      "mapper": {
        "retry": true,
        "maxRetries": 3,
        "interval": 60
      }
    }
  ],
  // ... rest of module config
}
```

**Error handler modules:**
- `builtin:Break` - Stop execution, optionally retry
- `builtin:Commit` - Commit partial results
- `builtin:Ignore` - Ignore error, continue
- `builtin:Resume` - Resume from error
- `builtin:Rollback` - Rollback changes

## Tools vs Scenarios

For simple single-action automations, consider using **Tools** instead:

```
Use mcp__make__tools_create for:
- Single module operations
- Simple input/output mappings
- Quick API actions

Use scenarios for:
- Multi-step workflows
- Complex routing logic
- Triggers and schedules
```

## Validation Checklist

Before creating a scenario:

- [ ] All module IDs are unique and sequential
- [ ] Connection IDs exist and are correct type
- [ ] Required fields are provided in mapper
- [ ] IML expressions reference valid module IDs
- [ ] Router conditions use correct operators
- [ ] Scheduling type and interval are valid
- [ ] Blueprint passes schema validation
- [ ] Each module config passes validation

## MCP Tools Reference

| Tool | Purpose |
|------|---------|
| `mcp__make__organizations_list` | Get your organization ID |
| `mcp__make__teams_list` | Get team IDs |
| `mcp__make__connections_list` | Get available connections |
| `mcp__make__apps_recommend` | Find apps for a task |
| `mcp__make__app-modules_list` | List modules in an app |
| `mcp__make__app-module_get` | Get module configuration schema |
| `mcp__make__validate_module_configuration` | Validate module setup |
| `mcp__make__validate_blueprint_schema` | Validate blueprint structure |
| `mcp__make__validate_scheduling_schema` | Validate scheduling |
| `mcp__make__scenarios_create` | Create new scenario |
| `mcp__make__scenarios_update` | Update existing scenario |
| `mcp__make__scenarios_activate` | Activate a scenario |
| `mcp__make__scenarios_deactivate` | Deactivate a scenario |
| `mcp__make__scenarios_run` | Execute a scenario |
| `mcp__make__tools_create` | Create a simple tool (single module) |
| `mcp__make__folders_list` | List folders for organization |
| `mcp__make__folders_create` | Create a folder |

## Troubleshooting

### "Connection not found"
- Verify connection ID exists using `connections_list`
- Check connection type matches module requirement

### "Invalid blueprint"
- Run `validate_blueprint_schema` to identify issues
- Check all required fields are present
- Verify JSON syntax is correct

### "Module configuration invalid"
- Use `app-module_get` to check required fields
- Run `validate_module_configuration` for detailed errors
- Ensure parameters vs mapper fields are correct

### "IML expression error"
- Verify referenced module ID exists and comes before current module
- Check field names match module output schema
- Test with hardcoded values first, then add IML
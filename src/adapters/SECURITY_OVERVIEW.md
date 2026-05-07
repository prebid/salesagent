# AdCP Security Architecture Overview

## Core Security Principles

The AdCP platform implements a multi-layered security architecture based on these principles:

1. **Principal-Based Isolation**: Every API request is authenticated and scoped to a principal
2. **Adapter-Enforced Boundaries**: Each ad server adapter enforces its own security perimeter
3. **No Shared State**: Adapters cannot access data from other principals
4. **Audit Everything**: All operations are logged with principal context

## Security Layers

### 1. Authentication Layer (MCP)

- Token-based authentication via MCP server
- Each principal has a unique, secret token
- Tokens are validated on every request

```python
# Token validation in run_server.py
auth_result = validate_auth(auth_header)
if not auth_result.is_valid:
    return JSONResponse({"error": "Invalid authentication"}, 401)
```

### 2. Principal Mapping Layer

- Each principal has platform-specific advertiser IDs
- Mappings are immutable after creation
- No principal can access another's mappings

```python
# Principal structure
{
    "principal_id": "unique_id",
    "name": "Company Name",
    "platform_mappings": {
        "gam": {"advertiser_id": "123456"},
        "triton": {"advertiser_id": "ABC123"}
    }
}
```

### 3. Database Layer

- Media buys are tagged with principal_id
- All queries filter by principal ownership
- Database enforces foreign key constraints

```python
# Ownership verification
def _verify_principal(principal_id: str, media_buy_id: str):
    if media_buy_id not in media_buys:
        raise ValueError(f"Media buy '{media_buy_id}' not found.")
    if media_buys[media_buy_id]["principal_id"] != principal_id:
        raise PermissionError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")
```

### 4. Adapter Layer

Each adapter enforces platform-specific security:
- **Google Ad Manager**: Uses advertiser_id for all API calls
- **Triton Digital**: Enforces station-level permissions
- **Mock**: Implements reference security model

## Security Boundaries

### What Principals CAN Do:
- Create media buys under their advertiser IDs
- Update their own campaigns and packages
- View delivery data for their campaigns
- Upload creatives to their line items

### What Principals CANNOT Do:
- Access any data from other principals
- Create resources under different advertiser IDs
- View competitive campaign information
- Modify platform-level settings

## Configuration Security

### Sensitive Data Protection

1. **API Keys & Tokens**:
   - Never stored in code
   - Use environment variables or secure vaults
   - Rotate regularly

2. **Service Accounts**:
   - Minimal required permissions
   - Separate accounts per environment
   - Audit access regularly

3. **Database Credentials**:
   - Encrypted at rest
   - SSL/TLS for connections
   - Principle of least privilege

## Audit & Compliance

### Logging Standards

Every operation logs:
- Principal name and ID
- Platform-specific advertiser ID
- Operation performed
- Timestamp
- Success/failure status

Example:
```
2024-01-15 10:30:45 [INFO] GoogleAdManager.create_media_buy for principal 'ACME Corp' (GAM advertiser ID: 123456789)
2024-01-15 10:30:46 [INFO] ✓ Created GAM Order ID: 987654321
```

### Compliance Considerations

- **Data Residency**: Respect regional data laws
- **Data Retention**: Follow platform policies
- **Access Control**: Regular access reviews
- **Incident Response**: Security breach procedures

## Best Practices

### For Operators:

1. **Regular Security Audits**:
   - Review principal mappings
   - Check adapter configurations
   - Validate access patterns

2. **Monitoring**:
   - Track failed authentication attempts
   - Alert on permission errors
   - Monitor for unusual patterns

3. **Updates**:
   - Keep adapters updated
   - Apply security patches promptly
   - Test security fixes

### For Developers:

1. **Security-First Design**:
   - Always validate principal ownership
   - Never trust client-provided IDs
   - Fail closed, not open

2. **Error Handling**:
   - Don't leak internal IDs
   - Log security errors
   - Return generic error messages

3. **Testing**:
   - Test security boundaries
   - Verify isolation
   - Check error cases

## Security Checklist

Before deploying a new adapter:

- [ ] Principal mapping validated
- [ ] API credentials secured
- [ ] Ownership checks implemented
- [ ] Audit logging complete
- [ ] Error messages sanitized
- [ ] Security documentation written
- [ ] Cross-principal testing done
- [ ] Rate limiting configured

## Incident Response

If a security issue is suspected:

1. **Immediate**: Revoke affected tokens
2. **Investigation**: Check audit logs
3. **Remediation**: Patch vulnerability
4. **Communication**: Notify affected parties
5. **Prevention**: Update procedures

## Future Enhancements

Planned security improvements:

1. **OAuth2 Integration**: Replace tokens with OAuth2 flows
2. **Role-Based Access**: Add user roles within principals
3. **Encryption**: Encrypt sensitive data at rest
4. **Key Rotation**: Automated credential rotation
5. **Security Scanning**: Automated vulnerability scanning

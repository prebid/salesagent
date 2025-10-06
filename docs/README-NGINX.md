# Nginx Routing Documentation

Complete guide to understanding and testing nginx routing behavior.

## Quick Links

📖 **[Routing Guide](./nginx-routing-guide.md)** - Detailed reference with routing tables, flows, and troubleshooting

🎨 **[Visual Diagrams](./nginx-routing-diagram.md)** - ASCII art diagrams showing request flow and decision trees

🧪 **[Test Script](../scripts/test_nginx_routing.py)** - Automated testing for nginx routing behavior

## TL;DR

We use nginx to route requests based on the **original domain** (from `Apx-Incoming-Host` header):

| Domain Type | Example | Shows |
|-------------|---------|-------|
| **Main** | `sales-agent.scope3.com` | Signup flow (OAuth) |
| **Tenant Subdomain** | `wonderstruck.sales-agent.scope3.com` | Tenant-specific MCP/A2A + admin |
| **External Virtual Host** | `test-agent.adcontextprotocol.org` | White-labeled landing page |

## Quick Test

After deploying nginx changes:

```bash
# Test all routes
python scripts/test_nginx_routing.py --env production

# Test specific domain type
python scripts/test_nginx_routing.py --filter "external" -v

# Expected output:
# ✅ PASS: External domain root → landing page
# ✅ PASS: External domain /mcp/ → 404
# ...
# ✅ ALL TESTS PASSED
```

## Common Scenarios

### I changed nginx.conf - how do I verify it works?

1. **Read the expected behavior**: `docs/nginx-routing-guide.md`
2. **Compare against your config**: Does your nginx.conf implement the routing tables?
3. **Deploy to staging/production**
4. **Run automated tests**: `python scripts/test_nginx_routing.py --env production`

### I need to understand why a domain shows the wrong page

1. **Check the visual diagrams**: `docs/nginx-routing-diagram.md`
2. **Trace the request flow** through the decision tree
3. **Identify which map/location block should match**
4. **Compare with actual nginx.conf**

### I'm onboarding and need to understand routing

Start here:
1. Read "Architecture Overview" in `nginx-routing-guide.md`
2. Look at the "Request Flow Overview" diagram in `nginx-routing-diagram.md`
3. Review the routing tables for each domain type

## File Organization

```
docs/
├── README-NGINX.md              # This file (overview)
├── nginx-routing-guide.md       # Complete reference guide
└── nginx-routing-diagram.md     # Visual diagrams

scripts/
└── test_nginx_routing.py        # Automated test script

config/
└── nginx/
    └── nginx.conf               # Actual nginx configuration
```

## Philosophy

**Problem**: Nginx routing is complex with multiple domain types, headers, and backends. Easy to break.

**Solution**:
- **Document** what should happen (routing guide)
- **Visualize** how requests flow (diagrams)
- **Test** that it actually works (test script)

Now you can:
- ✅ Understand what nginx should do
- ✅ Compare config against documentation
- ✅ Automatically verify behavior after changes
- ✅ Catch regressions before users report them

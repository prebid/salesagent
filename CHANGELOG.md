# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0](https://github.com/prebid/salesagent/compare/v1.2.0...v1.3.0) (2026-02-04)


### Features

* Add schema-driven adapter configuration ([#1007](https://github.com/prebid/salesagent/issues/1007)) ([e6324b6](https://github.com/prebid/salesagent/commit/e6324b6d4ebd1d7ce97ad012312d94a441ffc4d9))
* Display version in tenant landing page footer ([27780c5](https://github.com/prebid/salesagent/commit/27780c517cf63bb1d9404ae59f29287a7a67e39f))
* Display version in tenant landing page footer ([37015e6](https://github.com/prebid/salesagent/commit/37015e62be09e31bac0d8505f9310c2bfd48ab2a))


### Bug Fixes

* Add audit logging for get_products, update_media_buy, and update_performance_index ([9dfd39b](https://github.com/prebid/salesagent/commit/9dfd39b26e7838f89fd7f24569a12b73aa79a530))
* Add audit logging for get_products, update_media_buy, and update_performance_index ([d563dc8](https://github.com/prebid/salesagent/commit/d563dc8dc354cc412475410d583a8dcaddf6151a))
* Correct comment to match actual implementation (starts with, not contains) ([666f889](https://github.com/prebid/salesagent/commit/666f889a543cdf9f7623af6f0d041b5dff9d1644))
* Improve GAM creative-to-line-item matching for flexible naming templates ([126f0dd](https://github.com/prebid/salesagent/commit/126f0dd4ba2f61df47295f9eb09c61f24fc9efe1))
* Improve GAM creative-to-line-item matching for flexible naming templates ([7d85208](https://github.com/prebid/salesagent/commit/7d85208fb50378b8d654fb7256158ac11a9083cb))
* more tests ([88b0347](https://github.com/prebid/salesagent/commit/88b0347a155784b501ce03552ff1b736690f4e80))
* mypy ([0aeb111](https://github.com/prebid/salesagent/commit/0aeb111ceb25e3ba120bfbf0b40c4c6c3c461f4d))
* preserve format dimensions during media buy approval ([1bdc4a7](https://github.com/prebid/salesagent/commit/1bdc4a7fa0f6bd55748f4d57e1f9974edd5f6276))
* preserve format dimensions during media buy approval ([dc2429e](https://github.com/prebid/salesagent/commit/dc2429ed3b6d3e29e30b30fae6b9ac8b38d22444))
* Preserve signup flow state through OAuth redirect ([7e63052](https://github.com/prebid/salesagent/commit/7e63052fffad9b5a9b9e9d865073bb347f5febd6))
* Preserve signup flow state through OAuth redirect ([735211d](https://github.com/prebid/salesagent/commit/735211d2783bba2aff995e327f0cfea20ddce50b))
* reset tests to main ([03e32db](https://github.com/prebid/salesagent/commit/03e32db5eb97cb2c9791ba3fd555e686ad56608d))
* tests ([491e21e](https://github.com/prebid/salesagent/commit/491e21ea02e9f733773d48bf3bbb7ecb21212f3f))
* Update organization and repository names in ipr sig workflow ([3217152](https://github.com/prebid/salesagent/commit/32171525fd495394c28c2ba3bbb70122c68b55d6))
* Use dynamic dates in GAM pricing restriction tests ([bef48d1](https://github.com/prebid/salesagent/commit/bef48d1fdd4674529656f6ae2a577f89e9d739f9))
* Use dynamic dates in pricing integration tests ([a89dc8a](https://github.com/prebid/salesagent/commit/a89dc8ad7477e2ceb6a65750206f925060e571d1))


### Documentation

* Add PR naming guideline to CLAUDE.md ([34a87be](https://github.com/prebid/salesagent/commit/34a87be7d7104b54ff3e5570bffb4cf918156758))

## [1.2.0](https://github.com/prebid/salesagent/compare/v1.1.0...v1.2.0) (2026-01-29)


### Features

* Add get_adcp_capabilities tool for AdCP v3 compliance ([#973](https://github.com/prebid/salesagent/issues/973)) ([407a495](https://github.com/prebid/salesagent/commit/407a49550ea2a68d33429db05aafa10bc31c2369))


### Bug Fixes

* ipr policy should point to prebid.org ([1fad905](https://github.com/prebid/salesagent/commit/1fad905b2888b8e2cc023c541b3bd1fdc464d18f))
* Remove unnecessary trafficker_id requirement from GAM creatives_manager ([#975](https://github.com/prebid/salesagent/issues/975)) ([87fad8a](https://github.com/prebid/salesagent/commit/87fad8a9208fd1a64ff3e50822a99420e34552b7))


### Documentation

* fix repo loc ([03ffce8](https://github.com/prebid/salesagent/commit/03ffce8d5dcae6e43542ed9dc62890cb9787f4c6))

## [1.1.0](https://github.com/prebid/salesagent/compare/v1.0.0...v1.1.0) (2026-01-26)


### Features

* Add dry_run mode support for create/update media buy operations ([#970](https://github.com/prebid/salesagent/issues/970)) ([e9aac61](https://github.com/prebid/salesagent/commit/e9aac61b0cf08c1861d2b287853737cd68475dd2))


### Bug Fixes

* Add v2.x backward compatibility for pricing_options and clean up production logs ([#971](https://github.com/prebid/salesagent/issues/971)) ([0992131](https://github.com/prebid/salesagent/commit/0992131a5ccbd7b93a66409a262d20aeeb83ce28))

## [1.0.0](https://github.com/prebid/salesagent/compare/v0.9.3...v1.0.0) (2026-01-26)


### ⚠ BREAKING CHANGES

* This updates the AdCP library dependency from 2.14.0 to 3.0.0.

### Features

* Migrate to AdCP 3.0.0 library ([#968](https://github.com/prebid/salesagent/issues/968)) ([e4b31b2](https://github.com/prebid/salesagent/commit/e4b31b2ee2747db86c6d8dc352e58f0491d498ee))

## [0.9.3](https://github.com/prebid/salesagent/compare/v0.9.2...v0.9.3) (2026-01-25)


### Bug Fixes

* Handle product.format_ids as dicts in creative validation ([#965](https://github.com/prebid/salesagent/issues/965)) ([1c760e6](https://github.com/prebid/salesagent/commit/1c760e68c43a975505555214c7357f570c0df572))
* ignore url not configured when use mock adapters ([#943](https://github.com/prebid/salesagent/issues/943)) ([7f55e02](https://github.com/prebid/salesagent/commit/7f55e021d9bc53273c55b9394a4173a81fd62e69))

## [0.9.2](https://github.com/prebid/salesagent/compare/v0.9.1...v0.9.2) (2026-01-22)


### Bug Fixes

* Construct FormatId from DB creative's agent_url and format columns ([#961](https://github.com/prebid/salesagent/issues/961)) ([b22dbff](https://github.com/prebid/salesagent/commit/b22dbffe619a835231624fd5c3d751406c8cc5ff))

## [0.9.1](https://github.com/prebid/salesagent/compare/v0.9.0...v0.9.1) (2026-01-19)


### Bug Fixes

* Sync custom targeting keys to adapter_config during inventory sync ([fa187f9](https://github.com/prebid/salesagent/commit/fa187f9c4689b256df0b12bd25b4552832de99a9))
* Sync custom targeting keys to adapter_config during inventory sync ([d49badc](https://github.com/prebid/salesagent/commit/d49badc385de4377698ad58a76fa2751ee1621ed))

## [0.9.0](https://github.com/prebid/salesagent/compare/v0.8.0...v0.9.0) (2026-01-16)


### Features

* Add role name fallback for tracker detection and fix REDIRECT_URL macro ([4054133](https://github.com/prebid/salesagent/commit/405413384895081ad7f8d10d02680e04f14aa322))
* Add tracker_redirect support with REDIRECTION_URL macro ([6b34831](https://github.com/prebid/salesagent/commit/6b348318df29e0c856886a56097c2daa75c85d06))
* Add tracking pixel macro substitution for GAM adapter ([a3dd2d1](https://github.com/prebid/salesagent/commit/a3dd2d1d77146bb8d7b5edacf683c5f82c729e46))


### Bug Fixes

* Add isinstance check for list before append in creative_helpers ([8b42656](https://github.com/prebid/salesagent/commit/8b4265681c879059f790304b5ced1e105f121278))
* Improve click tracker and native creative tracking handling ([ade8de4](https://github.com/prebid/salesagent/commit/ade8de43cbcebab42d4109206e28e24b8506c054))
* Pass tenant_gemini_key as keyword argument to build_order_name_context ([5f4f1f1](https://github.com/prebid/salesagent/commit/5f4f1f1d9ba353b989370482b49674ccd04c173c))
* Restore click tracking URL support via destinationUrl ([c3eeab7](https://github.com/prebid/salesagent/commit/c3eeab7bf0c76f6470bdf0d497d530fdc78e29db))

## [0.8.0](https://github.com/prebid/salesagent/compare/v0.7.0...v0.8.0) (2026-01-14)


### Features

* update to adcp 2.18.0 with new assets field support ([6d19499](https://github.com/prebid/salesagent/commit/6d1949929b24f35299bd6ef60f73626fc9e10e55))
* update to adcp 2.18.0 with new assets field support ([4c94434](https://github.com/prebid/salesagent/commit/4c944340338ca0d12b1942e7bebf82d559670ec2))


### Bug Fixes

* Accept Authorization: Bearer header for MCP authentication ([#948](https://github.com/prebid/salesagent/issues/948)) ([a1ae3ff](https://github.com/prebid/salesagent/commit/a1ae3ffd6704a0dbf670964f021f305bb44be8b0))
* Fix CI test failures and security vulnerabilities ([f1e4c40](https://github.com/prebid/salesagent/commit/f1e4c40a172dbaddf74b951bf12052ddd7a0daff))
* Improve onboarding experience and resolve first-run issues ([#946](https://github.com/prebid/salesagent/issues/946)) ([e803e85](https://github.com/prebid/salesagent/commit/e803e8521d7cc3b6a40a0a359bfee0fb26e278e5))
* resolve mypy type errors for adcp 2.18.0 format assets ([7d0a021](https://github.com/prebid/salesagent/commit/7d0a0214cfe14aad667be0663e066fae3a1fb731))
* Update adcp to 2.18.0 for assets field support ([9693fcc](https://github.com/prebid/salesagent/commit/9693fccf5a56cde40e07045315b5cdf238bde0ae))
* Update adcp to 2.18.0 for assets field support ([ebfb27d](https://github.com/prebid/salesagent/commit/ebfb27dfdfc2586def28c43313bc5084cb89ede3))
* update tests for adcp 2.18.0 compatibility ([0a60085](https://github.com/prebid/salesagent/commit/0a60085e7c3bf402e008ed7c1c707da7c043bd66))
* update urllib3 and werkzeug to fix security vulnerabilities ([9490db8](https://github.com/prebid/salesagent/commit/9490db82b9cb369b31d7ed0af0ea9d8a2b6cf6df))
* use dynamic adcp version in e2e tests instead of hardcoded 2.5.0 ([ecee6f2](https://github.com/prebid/salesagent/commit/ecee6f297c47be85a348620e2f642e22ab619525))


### Code Refactoring

* rename asset_req to asset_spec for clarity ([f15c9ea](https://github.com/prebid/salesagent/commit/f15c9ea944ff9c80969909620ddf8ae82657207a))


### Documentation

* clarify that repeatable groups were never supported in asset extraction ([dbee49e](https://github.com/prebid/salesagent/commit/dbee49ef5d847d90127fe90f026dc5fe9e60f9ca))

## [0.7.0](https://github.com/prebid/salesagent/compare/v0.6.0...v0.7.0) (2026-01-08)


### Features

* Add tenant-configurable favicon support ([#940](https://github.com/prebid/salesagent/issues/940)) ([f8b1696](https://github.com/prebid/salesagent/commit/f8b1696f2939314d6c3973eed1a7b66108b5ebc1))


### Bug Fixes

* a2a bugs with media buy and media buy delivery ([c5325b9](https://github.com/prebid/salesagent/commit/c5325b9982f4d3afa1559542cac8e4a023834fba))
* a2a bugs with media buy and media buy delivery ([ea98357](https://github.com/prebid/salesagent/commit/ea98357d30e6ded327cc0eda8ed8ea4d2c91aaa5))
* Add security audit to CI and upgrade fastmcp ([#941](https://github.com/prebid/salesagent/issues/941)) ([ec592ed](https://github.com/prebid/salesagent/commit/ec592edf3789b7e3b92a7060ca89e29d1721dfab))
* Include empty pricing_options in serialization for anonymous users ([#939](https://github.com/prebid/salesagent/issues/939)) ([4e57265](https://github.com/prebid/salesagent/commit/4e57265631d73258959dcb8021601d4346599ae9))
* Set default role to admin for SSO auto-provisioned users ([#937](https://github.com/prebid/salesagent/issues/937)) ([e64440c](https://github.com/prebid/salesagent/commit/e64440c2c5253ab9b387132403d5ab1ae66378b0))

## [0.6.0](https://github.com/prebid/salesagent/compare/v0.5.0...v0.6.0) (2026-01-05)


### Features

* Add GAM placement targeting for creative-level targeting (adcp[#208](https://github.com/prebid/salesagent/issues/208)) ([#915](https://github.com/prebid/salesagent/issues/915)) ([b2f9585](https://github.com/prebid/salesagent/commit/b2f9585660eee9098c26f22adcf49636e1472ca7))
* apply suggestions ([362513f](https://github.com/prebid/salesagent/commit/362513fdfdcae1323d48b3d3ec2076142c131c66))
* apply suggestions ([9b75990](https://github.com/prebid/salesagent/commit/9b759903506f7cd9b49be5de068b65e848351c28))
* improve e2e test for a2a push notification delivery v2 ([f9008b9](https://github.com/prebid/salesagent/commit/f9008b9ccb4e96fdcbaba93bc2234e2f2f1804c5))
* Make SSO optional for multi-tenant deployments ([#931](https://github.com/prebid/salesagent/issues/931)) ([8ac80a1](https://github.com/prebid/salesagent/commit/8ac80a143957dcf29e8b51457ec4f4e4cf44237d))
* migrate push notification sending for media_buy ([6fa4cda](https://github.com/prebid/salesagent/commit/6fa4cda0a587d7d4846f22641c5b6f44dab13298))
* undo unrelated changes ([9a7e45f](https://github.com/prebid/salesagent/commit/9a7e45f38f130b9de6efbb1b362dba2555ba4e62))
* update webhook delivery function to support both mcp and a2a payloads ([7f41d98](https://github.com/prebid/salesagent/commit/7f41d989ad254280b6cfd6c138352e4404242bff))
* update webhook delivery function to support both mcp and a2a payloads ([27b2eaa](https://github.com/prebid/salesagent/commit/27b2eaad01a7ad94939de710743f39bb79d2e61d))
* wip ([0713543](https://github.com/prebid/salesagent/commit/07135438371946932469d6676bc9bbd45add0acc))


### Bug Fixes

* adcp version; media buy status change; media buy delivery look up ([41dd1dc](https://github.com/prebid/salesagent/commit/41dd1dc5a9fae67020e106589d9471a5eb8705e6))
* Add Fly.io header middleware for proper HTTPS detection ([#920](https://github.com/prebid/salesagent/issues/920)) ([a115fc9](https://github.com/prebid/salesagent/commit/a115fc9f0e0f1a4f71385843ed80e074833b7482))
* Add multi-admin domain support for cross-domain OAuth ([#919](https://github.com/prebid/salesagent/issues/919)) ([f373ebb](https://github.com/prebid/salesagent/commit/f373ebb1350fe55798b270a9ad8155c905593e5f))
* Clear session before OAuth to prevent stale cookie conflicts ([#924](https://github.com/prebid/salesagent/issues/924)) ([addab84](https://github.com/prebid/salesagent/commit/addab84e7d3b280d3d157c416fb988d468b14d87))
* Correct middleware ordering for Fly.io header processing ([#921](https://github.com/prebid/salesagent/issues/921)) ([c4d373d](https://github.com/prebid/salesagent/commit/c4d373dcc04ccae7074e426f24997f2c4d5ab212))
* e2e webhook delivery check ([1599266](https://github.com/prebid/salesagent/commit/159926689db9a986ef3bb8ef55359a864b3cd3b9))
* Explicitly save session on OAuth redirect to persist state cookie ([#928](https://github.com/prebid/salesagent/issues/928)) ([e78ae67](https://github.com/prebid/salesagent/commit/e78ae67a1cd2f7638117a87f6a37823e678d2c8f))
* Fix list_creatives enum serialization and invalid creative count ([#930](https://github.com/prebid/salesagent/issues/930)) ([3d9c643](https://github.com/prebid/salesagent/commit/3d9c64368a8956f8acc7948201be1c55c31906a5))
* improve tests ([676690b](https://github.com/prebid/salesagent/commit/676690bf82ef3aa750e553e0e9f3a75933344ec1))
* integration test v2 ([756d6d4](https://github.com/prebid/salesagent/commit/756d6d4cf7381fd63c9acdd6a2721bc33ac3fbcf))
* integrations ([8050605](https://github.com/prebid/salesagent/commit/805060535f16c5fb3bf7ce3d5168fa91af27efff))
* link validator check for cyclic bugs ([91fe6f7](https://github.com/prebid/salesagent/commit/91fe6f70e8d7ee0919bc79f72657c8bd76b3ae02))
* mypy failures ([79de36d](https://github.com/prebid/salesagent/commit/79de36d4393aac56614e19b83a32a2963f028a24))
* Preserve tenant context on OAuth callback errors ([#918](https://github.com/prebid/salesagent/issues/918)) ([c82760b](https://github.com/prebid/salesagent/commit/c82760beddae4a9c4f376ae22395b680a2412466))
* Preserve X-Forwarded-Proto from Fly.io through nginx ([#922](https://github.com/prebid/salesagent/issues/922)) ([5eddd36](https://github.com/prebid/salesagent/commit/5eddd36dd2d2e118e47db9f1f810470f2bde89ab))
* Prevent redirect loop for super admins accessing /admin/ ([#929](https://github.com/prebid/salesagent/issues/929)) ([95d7cac](https://github.com/prebid/salesagent/commit/95d7cac07d98fe06b292f90de6a65f04689e8ab8))
* Restore deleted migration to fix Fly.io deploy ([#914](https://github.com/prebid/salesagent/issues/914)) ([2cfbccc](https://github.com/prebid/salesagent/commit/2cfbccce8a0a2850f30d22f73970b3642cc28f1a))
* Reuse unwrapped brand_manifest for policy checks ([#932](https://github.com/prebid/salesagent/issues/932)) ([#935](https://github.com/prebid/salesagent/issues/935)) ([03ba408](https://github.com/prebid/salesagent/commit/03ba40841de09a85656f1d17cc864348ab8836eb))
* Route multi-tenant subdomain requests to tenant-specific login ([#916](https://github.com/prebid/salesagent/issues/916)) ([c0152db](https://github.com/prebid/salesagent/commit/c0152dbce4d1965cf7f500e366cc5013092c6e87))
* Run database migrations automatically on docker compose up ([#933](https://github.com/prebid/salesagent/issues/933)) ([c5c73a8](https://github.com/prebid/salesagent/commit/c5c73a80f356ca5c149026938ffdd7ae6b515c94))
* run_all_tests ci ([3a1b1d2](https://github.com/prebid/salesagent/commit/3a1b1d2f05780ad8990d149073d8fb38c6838aa2))
* Show full values in Pydantic extra_forbidden errors ([#912](https://github.com/prebid/salesagent/issues/912)) ([165d985](https://github.com/prebid/salesagent/commit/165d985805edd5b35bdfbfec0768150f1b7a4696))
* Task and TaskStatusUpdate serializations ([a8e7792](https://github.com/prebid/salesagent/commit/a8e77926bfa5237e7dc2bdf74ba09d092bba7488))
* Use global OAuth as fallback, not setup mode for multi-tenant ([#917](https://github.com/prebid/salesagent/issues/917)) ([ef63349](https://github.com/prebid/salesagent/commit/ef63349191e0f8002d304db6f9feda92b8b0cbc4))


### Documentation

* Update Docker Compose documentation to reflect nginx proxy architecture ([#934](https://github.com/prebid/salesagent/issues/934)) ([5503768](https://github.com/prebid/salesagent/commit/55037689c4ed5e58bebf24e65710c2ae8e646349))

## [0.5.0](https://github.com/prebid/salesagent/compare/v0.4.1...v0.5.0) (2026-01-01)


### Features

* Add dynamic per-tenant OIDC/SSO authentication ([#903](https://github.com/prebid/salesagent/issues/903)) ([ed05a41](https://github.com/prebid/salesagent/commit/ed05a4131ea4fffa212ab1e72a243650d0a493b5))
* Add format template picker UI for AdCP 2.5 parameterized formats ([#782](https://github.com/prebid/salesagent/issues/782)) ([#882](https://github.com/prebid/salesagent/issues/882)) ([532657e](https://github.com/prebid/salesagent/commit/532657ec6b796f12d40a2c41860b67bc4c0fca62))
* Add vidium MCP server to local configuration ([#904](https://github.com/prebid/salesagent/issues/904)) ([ebcfdd1](https://github.com/prebid/salesagent/commit/ebcfdd134afd1e752c43248454203792409d4092))
* Convert advertising channel from single to multi-select ([#897](https://github.com/prebid/salesagent/issues/897)) ([a1aa8e4](https://github.com/prebid/salesagent/commit/a1aa8e42489726f610986d7b1822fe6cd4596968))
* Display sales agent version in agent card ([#902](https://github.com/prebid/salesagent/issues/902)) ([663702b](https://github.com/prebid/salesagent/commit/663702b3095e1b860b9bb20bf569148c993b0f35))
* Implement AI product ranking with simplified catalog ([#906](https://github.com/prebid/salesagent/issues/906)) ([d59e76b](https://github.com/prebid/salesagent/commit/d59e76b5f04cdf820118a84f41131e174fa0efda))
* Simplify user authorization with User records as primary auth method ([#907](https://github.com/prebid/salesagent/issues/907)) ([504b489](https://github.com/prebid/salesagent/commit/504b4897167cce98b05517787904cb3ffeeeaf12))


### Bug Fixes

* Simplify Docker Compose setup to fix mount errors ([#910](https://github.com/prebid/salesagent/issues/910)) ([723b0b2](https://github.com/prebid/salesagent/commit/723b0b2858a27cdd4747be733f2442ac6f7f08de))
* Single-tenant deployment and SSO configuration ([#908](https://github.com/prebid/salesagent/issues/908)) ([e725781](https://github.com/prebid/salesagent/commit/e7257818aa041577f328b1426384fc05df45f96e))
* src.core.format_spec_cache undefined ([#901](https://github.com/prebid/salesagent/issues/901)) ([e3e701c](https://github.com/prebid/salesagent/commit/e3e701c69339402802dd3e5741495047083a41cf))
* Update docs links and fix alembic migrations ([#911](https://github.com/prebid/salesagent/issues/911)) ([e498a43](https://github.com/prebid/salesagent/commit/e498a43139e243bfed91c5fb599ad8f59bd2be69))
* Use pull_request_target for PR title check on fork PRs ([#909](https://github.com/prebid/salesagent/issues/909)) ([bb9817d](https://github.com/prebid/salesagent/commit/bb9817dcdb95d890d79f364b40cff8cf395b3db9))


### Documentation

* Clarify SUPER_ADMIN_EMAILS is optional with per-tenant OIDC ([#905](https://github.com/prebid/salesagent/issues/905)) ([399b255](https://github.com/prebid/salesagent/commit/399b2550dec405a42b9c41f5491b7e9cb67a952d))

## [0.4.1](https://github.com/prebid/salesagent/compare/v0.4.0...v0.4.1) (2025-12-29)


### Documentation

* Add Fly Managed Postgres option to deployment guide ([#894](https://github.com/prebid/salesagent/issues/894)) ([6bf6ce9](https://github.com/prebid/salesagent/commit/6bf6ce91041b372de20513acdd6019b3096a46c1))
* Fix GCP Cloud Run deployment walkthrough ([#896](https://github.com/prebid/salesagent/issues/896)) ([10a9674](https://github.com/prebid/salesagent/commit/10a96743080a6767e1936d41da9cb7845b304f6c))

## [0.4.0](https://github.com/prebid/salesagent/compare/v0.3.0...v0.4.0) (2025-12-28)


### Features

* Add GAM currency detection and Budget Controls integration ([#887](https://github.com/prebid/salesagent/issues/887)) ([f7539e3](https://github.com/prebid/salesagent/commit/f7539e33d77d4fbe589301f9cb095b30a8298a5a))
* Consolidate Docker entrypoint to use Python directly ([#880](https://github.com/prebid/salesagent/issues/880)) ([a12b19d](https://github.com/prebid/salesagent/commit/a12b19dc7e39b0017fa4a7fd90941ff08eebdaf3))
* Default to production setup, make demo mode opt-in ([#883](https://github.com/prebid/salesagent/issues/883)) ([580bcfe](https://github.com/prebid/salesagent/commit/580bcfe90b655f702349c77631c1999355238b65))
* Restrict currency selection to GAM-supported currencies ([#890](https://github.com/prebid/salesagent/issues/890)) ([1076539](https://github.com/prebid/salesagent/commit/10765399d45d1a9705fee38f48ec8ccf76c01c95))


### Bug Fixes

* Only set SESSION_COOKIE_DOMAIN in multi-tenant mode ([#886](https://github.com/prebid/salesagent/issues/886)) ([dfbb577](https://github.com/prebid/salesagent/commit/dfbb577532ef30301613cd2ddf86f3519b483375))


### Code Refactoring

* Reorganize admin settings navigation and elevate publisher management ([#892](https://github.com/prebid/salesagent/issues/892)) ([2f5e9e6](https://github.com/prebid/salesagent/commit/2f5e9e6c638e7bbf0ceca1d9bd3b547b8406fa68))


### Documentation

* Reorganize documentation with automatic link checking ([#879](https://github.com/prebid/salesagent/issues/879)) ([a8f57a6](https://github.com/prebid/salesagent/commit/a8f57a65967214b483aa927603bfdd23341437f2))

## [0.3.0](https://github.com/prebid/salesagent/compare/v0.2.1...v0.3.0) (2025-12-26)


### Features

* Add Docker Hub as secondary container registry ([#878](https://github.com/prebid/salesagent/issues/878)) ([71e7d2f](https://github.com/prebid/salesagent/commit/71e7d2f286b84abe9b875908ffb3a29269731954))
* Enhance AdCP 2.5 creative rotation weight support with improved error handling ([#876](https://github.com/prebid/salesagent/issues/876)) ([d226b58](https://github.com/prebid/salesagent/commit/d226b58e6ad0ea0d694eaca601fe0011f65e2b0b))

## [0.2.1](https://github.com/prebid/salesagent/compare/v0.2.0...v0.2.1) (2025-12-25)


### Bug Fixes

* Use www-data user in nginx-simple.conf for Debian compatibility ([#874](https://github.com/prebid/salesagent/issues/874)) ([81f6e42](https://github.com/prebid/salesagent/commit/81f6e42c4ef239d085a36ca70185e05fe4beb508))

## [0.2.0](https://github.com/prebid/salesagent/compare/v0.1.0...v0.2.0) (2025-12-24)


### Features

* Improve Docker quickstart - ARM64 support, better docs, fail-fast validation ([#859](https://github.com/prebid/salesagent/issues/859)) ([ba3f81a](https://github.com/prebid/salesagent/commit/ba3f81a4e82010ad0d129269fea1086323829cb4))
* Improve single-tenant mode UX and Docker quickstart ([#868](https://github.com/prebid/salesagent/issues/868)) ([8559f8d](https://github.com/prebid/salesagent/commit/8559f8d4cb201f9bc83f744ea2660d9a832bb58a))
* Pydantic AI multi-provider integration with admin UI ([#860](https://github.com/prebid/salesagent/issues/860)) ([1ff0366](https://github.com/prebid/salesagent/commit/1ff03663fdc6514d74869fadc0601b3bd427b6d3))
* show access token directly in advertisers table ([#867](https://github.com/prebid/salesagent/issues/867)) ([ceac7b0](https://github.com/prebid/salesagent/commit/ceac7b070ec6098caa1a26dc58a908a94b484de8))


### Bug Fixes

* enforce tenant human_review_required for media buy approval ([#866](https://github.com/prebid/salesagent/issues/866)) ([92c562e](https://github.com/prebid/salesagent/commit/92c562e8ccd0680a0daf08851a63affa662e74ab))
* Fix/format ids type handling for the format_ids in the products table ([#864](https://github.com/prebid/salesagent/issues/864)) ([bd65beb](https://github.com/prebid/salesagent/commit/bd65beb8763f6ec0ca1af6333031b12fbee2e139))
* Update release-please to use manifest mode (v4 config) ([925a1b2](https://github.com/prebid/salesagent/commit/925a1b2c9bbfc7231049f6da13bed403d9ff13ca))


### Code Refactoring

* align schemas with AdCP library specifications ([#856](https://github.com/prebid/salesagent/issues/856)) ([3c60413](https://github.com/prebid/salesagent/commit/3c6041302cd6921ea3c26bdf960198b3c974d3ad))


### Documentation

* Add Conventional Commits guidance to CLAUDE.md ([4578eab](https://github.com/prebid/salesagent/commit/4578eabdf6f5511c8b0e26bd23a6f9268642e121))
* Add platform-specific deployment guides and Cloud SQL improvements ([#869](https://github.com/prebid/salesagent/issues/869)) ([38626f8](https://github.com/prebid/salesagent/commit/38626f891916c27adb4efd783ee74a23bc8ac86e))
* Update quickstart to use published Docker images ([#857](https://github.com/prebid/salesagent/issues/857)) ([435d6d2](https://github.com/prebid/salesagent/commit/435d6d287a55f3ebae057e5e47a045560bfe66fd))
* Update quickstart to use published Docker images ([#857](https://github.com/prebid/salesagent/issues/857)) ([#861](https://github.com/prebid/salesagent/issues/861)) ([7db5c94](https://github.com/prebid/salesagent/commit/7db5c94331c61d2945a419790e30f01df4cefd05))

## 0.1.0 (2025-12-20)


### ⚠ BREAKING CHANGES

* Media buy creation now FAILS when creatives are missing required fields (URL, dimensions) instead of silently skipping them.

### Features

* Add AdCP 2.5 extension to A2A agent card ([#783](https://github.com/prebid/salesagent/issues/783)) ([a979cb6](https://github.com/prebid/salesagent/commit/a979cb6741395113d5c9e2a79209c53f5e029f8f))
* add auth_header and timeout columns to creative_agents table ([#714](https://github.com/prebid/salesagent/issues/714)) ([64eecd8](https://github.com/prebid/salesagent/commit/64eecd834f0347aeee46b961c2f8730b37da207f))
* add background scheduler to auto-transition media buy statuses based on flight dates ([4af1343](https://github.com/prebid/salesagent/commit/4af13438cbc94f459880e983b9b402cfae621cb9))
* add background scheduler to auto-transition media buy statuses based on flight dates ([d6f8d78](https://github.com/prebid/salesagent/commit/d6f8d787303e3890422face75a406f167d345d60))
* Add brand manifest policy system for flexible product discovery ([#663](https://github.com/prebid/salesagent/issues/663)) ([1c00e1d](https://github.com/prebid/salesagent/commit/1c00e1da7a24bba3b64e20c6534523d336e7815b))
* Add brand manifest policy UI dropdown in Admin ([#726](https://github.com/prebid/salesagent/issues/726)) ([55d2414](https://github.com/prebid/salesagent/commit/55d24145e8641c59baf9fa93330822ebd697910f))
* add commitizen for automated version management ([#666](https://github.com/prebid/salesagent/issues/666)) ([4c49051](https://github.com/prebid/salesagent/commit/4c49051cdea309b2ef20fd5eeb28fd6e3f5890ce))
* Add creative format size filtering with inventory-based suggestions ([#690](https://github.com/prebid/salesagent/issues/690)) ([ced6466](https://github.com/prebid/salesagent/commit/ced64664ff225d1c9c0ca3dcbd5e3a6fc90e473d))
* add date range validation and testing for validation ([9706fd1](https://github.com/prebid/salesagent/commit/9706fd1f0f9d65dd26628cb82986d68595414508))
* Add hierarchical product picker with search and caching ([#707](https://github.com/prebid/salesagent/issues/707)) ([6a6c23d](https://github.com/prebid/salesagent/commit/6a6c23d0a194862f84af4052d9daa58fa2f02183))
* Add inventory profiles for reusable inventory configuration ([#722](https://github.com/prebid/salesagent/issues/722)) ([ceb2363](https://github.com/prebid/salesagent/commit/ceb2363ca7f1879bb3f467d302ee44905194d40d))
* Add manual delivery webhook trigger to admin UI ([f91d55e](https://github.com/prebid/salesagent/commit/f91d55eca789cd01f969eeca521349699bda6713))
* Add manual delivery webhook trigger to admin UI ([e95d6f4](https://github.com/prebid/salesagent/commit/e95d6f4a0b21011e16225104ecd3bc94ba521fe5))
* Add real-time custom targeting values endpoint and visual selector widget ([#678](https://github.com/prebid/salesagent/issues/678)) ([ebd89b9](https://github.com/prebid/salesagent/commit/ebd89b97868e9477ae624010304b417bd5b8d55f))
* Add signals agent registry with unified MCP client ([#621](https://github.com/prebid/salesagent/issues/621)) ([9a15431](https://github.com/prebid/salesagent/commit/9a15431f2a36663e93de4d2a94dcc7f7aef954c6))
* alphabetize targeting keys/values and show display names ([#687](https://github.com/prebid/salesagent/issues/687)) ([c6be06d](https://github.com/prebid/salesagent/commit/c6be06d045bf4a4ff8063044827ef0006c9525dd))
* Auto-download AdCP schemas on workspace startup ([#616](https://github.com/prebid/salesagent/issues/616)) ([94c3876](https://github.com/prebid/salesagent/commit/94c3876ae67bc0759ef823d43e4028d765d28cf1))
* calculate clicks and ctr ([ebe7d66](https://github.com/prebid/salesagent/commit/ebe7d66290a9f5cecff0be783c2d2ff3c376426a))
* enforce strict AdCP v1 spec compliance for Creative model (BREAKING CHANGE) ([#706](https://github.com/prebid/salesagent/issues/706)) ([ff1cbc4](https://github.com/prebid/salesagent/commit/ff1cbc4732e5038b0493cfc90d1e2964de034707))
* improve product workflow - always show formats and descriptive targeting values ([#688](https://github.com/prebid/salesagent/issues/688)) ([4530f25](https://github.com/prebid/salesagent/commit/4530f253d24779aa4ef4f0ee3d527d3258bb28f3))
* Publish Docker images on release ([#855](https://github.com/prebid/salesagent/issues/855)) ([47e88e3](https://github.com/prebid/salesagent/commit/47e88e3fb1a35bd396bb656605d69b9a43d7ba41))
* refactor and add integration and e2e tests for delivery metrics webhooks ([3df36de](https://github.com/prebid/salesagent/commit/3df36dedbab6c53de1bcdf4919403aa69ecc9343))
* refactor webhook deliveries ([f1302ba](https://github.com/prebid/salesagent/commit/f1302ba66be517a999d0e00c78bce16046b6aebb))
* Remove Scope3 dependencies - make codebase vendor-neutral ([#668](https://github.com/prebid/salesagent/issues/668)) ([de503bf](https://github.com/prebid/salesagent/commit/de503bfda0e275cfc2273b93b757c47a9cbccd2c))
* Simplify targeting selector to match existing UI patterns ([#679](https://github.com/prebid/salesagent/issues/679)) ([ce76f8e](https://github.com/prebid/salesagent/commit/ce76f8e2ca01070f3f281aa5f9a69d83789af768))
* support application level context ([#735](https://github.com/prebid/salesagent/issues/735)) ([ea6891d](https://github.com/prebid/salesagent/commit/ea6891d8091f2e178330802293859bf93b3838bc))
* Update budget handling to match AdCP v2.2.0 specification ([#635](https://github.com/prebid/salesagent/issues/635)) ([0a9dd4a](https://github.com/prebid/salesagent/commit/0a9dd4a160deca71508aa83e3e8f5b56b5198e14))


### Bug Fixes

* 'Select All' buttons in Create Product page by fixing JS scope ([5f5553a](https://github.com/prebid/salesagent/commit/5f5553a9e68219300f19cbec891bd16d3e9cea1f))
* 'Select All' buttons in Create Product page by fixing JS scope ([6bcca14](https://github.com/prebid/salesagent/commit/6bcca145aaf8711b7176c94e397fe429276d8bc7))
* Achieve 100% mypy compliance in src/ directory - 881 errors to 0 ([#662](https://github.com/prebid/salesagent/issues/662)) ([d7f4711](https://github.com/prebid/salesagent/commit/d7f47112fa0fe221447bd470d4daeb4783f86b75))
* ad unit format button, targeting selector crash, and service account auth ([#723](https://github.com/prebid/salesagent/issues/723)) ([83bd497](https://github.com/prebid/salesagent/commit/83bd497469eaa30eeba28e3960137fc6ebbbe498))
* AdCP responses now exclude None values in JSON serialization ([#642](https://github.com/prebid/salesagent/issues/642)) ([c3fa69a](https://github.com/prebid/salesagent/commit/c3fa69a511db5942ee307dcad6c1fe5cf6b06246))
* AdCP responses now properly omit null/empty optional fields ([#638](https://github.com/prebid/salesagent/issues/638)) ([ab7c4cd](https://github.com/prebid/salesagent/commit/ab7c4cdaed47c3f3ce85de845914051d3a08197d))
* Add /admin prefix to OAuth redirect URI for nginx routing ([#651](https://github.com/prebid/salesagent/issues/651)) ([a95a534](https://github.com/prebid/salesagent/commit/a95a5344d38667d0e4209dff3f7345d637ed8fbe))
* Add content hash verification to prevent meta file noise ([#659](https://github.com/prebid/salesagent/issues/659)) ([20b0a16](https://github.com/prebid/salesagent/commit/20b0a165b7fea7a8da33840806bc03ef612fc32d))
* add e2e tests for get_media_buy_delivery direct request ([1263a81](https://github.com/prebid/salesagent/commit/1263a8141543b72ac10ef0d8235cddb688a75cf7))
* Add logging + fix targeting browser sync button ([#677](https://github.com/prebid/salesagent/issues/677)) ([bdf19cc](https://github.com/prebid/salesagent/commit/bdf19cccfe177429f0420793ee2eae3206eed157))
* Add missing /api/tenant/&lt;tenant_id&gt;/products endpoint ([9dc4bdc](https://github.com/prebid/salesagent/commit/9dc4bdcf3787a40a921d1c5374a2f3da1776c0fb))
* Add missing activity feed and audit logs to manual approval path ([#729](https://github.com/prebid/salesagent/issues/729)) ([114778c](https://github.com/prebid/salesagent/commit/114778c85d009333d30b7640b623a11bd8ee0d6f))
* Add missing adapter_type to SyncJob creation ([fb0fb79](https://github.com/prebid/salesagent/commit/fb0fb7905699503087180af91acf8190c2fa4bfa))
* Add null safety checks for audience.type and audience.segment_type ([#682](https://github.com/prebid/salesagent/issues/682)) ([b8e6e77](https://github.com/prebid/salesagent/commit/b8e6e77a4aea4a2589e7e1fddc73f6346e2729c2))
* add pricing to delivery ([78eab1e](https://github.com/prebid/salesagent/commit/78eab1e05a60e1ac86cdb340c7ec0708078d33bb))
* Add timeout to discover_ad_units to prevent stuck syncs ([56457ad](https://github.com/prebid/salesagent/commit/56457ad07c329064b451869b2e25134a401bb0d3))
* add type field to audience segments API for filtering ([28302f2](https://github.com/prebid/salesagent/commit/28302f27964287bdacee4261d97b4ecc7467de11))
* add type field to audience segments API for filtering ([474df9a](https://github.com/prebid/salesagent/commit/474df9a4545d0ded0873d22303fe8bba4824d59f))
* advertiser creation ([4e9e32d](https://github.com/prebid/salesagent/commit/4e9e32d35e0a65e57c5b1c218a7c38e8dee06a83))
* advertiser creation ([d323477](https://github.com/prebid/salesagent/commit/d323477dc424eef8655a76d5fa43e9c6f3ad644b))
* apply type filter when fetching inventory by IDs ([3fc3ded](https://github.com/prebid/salesagent/commit/3fc3ded211a5137c932fbd20be18b36a35a19e46))
* approval flow ([ee2e90a](https://github.com/prebid/salesagent/commit/ee2e90acfb204478b1c1bcc5c52e07ee97e78cce))
* attempt to fix e2e test in ci ([8c269a8](https://github.com/prebid/salesagent/commit/8c269a8ffa56752365f5ebf113253f5ce6ded7fc))
* Auto-create default principal and improve setup output ([#849](https://github.com/prebid/salesagent/issues/849)) ([0c222f3](https://github.com/prebid/salesagent/commit/0c222f3afdad4bf4358e3987b04d2bd64ce517d7))
* Auto-create user records for authorized emails on tenant login ([#492](https://github.com/prebid/salesagent/issues/492)) ([454eb8f](https://github.com/prebid/salesagent/commit/454eb8ffbb015b63e958f86d17361c0462358b32))
* Check super admin status before signup flow redirect ([#674](https://github.com/prebid/salesagent/issues/674)) ([e5dfb8d](https://github.com/prebid/salesagent/commit/e5dfb8dc4c98bf426f463f01992b31aab9bab3de))
* Clean up smoke tests and resolve warnings ([#629](https://github.com/prebid/salesagent/issues/629)) ([73cbc99](https://github.com/prebid/salesagent/commit/73cbc99d4ed8c8385b0b09b0ce5e43fa7ecc006b))
* Complete /admin prefix handling for all API calls ([#736](https://github.com/prebid/salesagent/issues/736)) ([4c20c9c](https://github.com/prebid/salesagent/commit/4c20c9c6e68d953f1548fe2253338b4d67dc18e1))
* Convert FormatReference to FormatId in MediaPackage reconstruction ([#656](https://github.com/prebid/salesagent/issues/656)) ([7c24705](https://github.com/prebid/salesagent/commit/7c247053d94abbce15331b4df05069636ad1409f))
* Convert summary dict to JSON string in sync completion ([3318ee0](https://github.com/prebid/salesagent/commit/3318ee0bed23bb1a21d2f2cb8870d73d59234dac))
* convert to utc ([bcb54f0](https://github.com/prebid/salesagent/commit/bcb54f01bba60ac6862332942d09ee332387b3a5))
* Correct AdManagerClient signature for service account auth ([#571](https://github.com/prebid/salesagent/issues/571)) ([bcb1686](https://github.com/prebid/salesagent/commit/bcb1686fa8c23492db73a63e87d088f5ae6c6246)), closes [#570](https://github.com/prebid/salesagent/issues/570)
* Correct API field name mismatch in targeting selector widget ([#681](https://github.com/prebid/salesagent/issues/681)) ([9573749](https://github.com/prebid/salesagent/commit/9573749beb05d260b0786479c68b479c85807c56))
* correct creative agent URL typo (creatives → creative) ([#844](https://github.com/prebid/salesagent/issues/844)) ([f29659b](https://github.com/prebid/salesagent/commit/f29659bbe65b2f3e161a95f44749fb89b348390e))
* correct inventory search endpoint and parameters in unified view ([201fd4f](https://github.com/prebid/salesagent/commit/201fd4fdc90ffc6cb572275b64fae03d4dda4b26))
* correct inventory search endpoint and parameters in unified view ([5532adb](https://github.com/prebid/salesagent/commit/5532adb5d2f6e5332aa3db3fb90029aefc0f551e))
* Correct tenant context ordering in update_media_buy ([#773](https://github.com/prebid/salesagent/issues/773)) ([2c2d9b1](https://github.com/prebid/salesagent/commit/2c2d9b171df6db044f652d81a927baff2977e108))
* Create mock properties only for mock adapters ([#854](https://github.com/prebid/salesagent/issues/854)) ([efdcfca](https://github.com/prebid/salesagent/commit/efdcfcad626d61b1b76ef96979d4ed3d8a5ec47a))
* creative agent url check; allow to fallback to /mcp when creating mcp client ([09bc1ac](https://github.com/prebid/salesagent/commit/09bc1ac6782faf1362ba253f23785c842aa771d7))
* creative agent url check; allow to fallback to /mcp when creating mcp client ([6bf221f](https://github.com/prebid/salesagent/commit/6bf221f501fb6f700d2092bf83cca58884deb365))
* creative approval/rejection webhook delivery ([9062449](https://github.com/prebid/salesagent/commit/9062449959bfcca02f1d3377b5f9f8c962917d57))
* Creative management - reject invalid creatives ([#460](https://github.com/prebid/salesagent/issues/460)) ([1540de3](https://github.com/prebid/salesagent/commit/1540de3946f6de9b22fd37e9b08077f006c86894))
* Default publisher_properties to 'all' when not specified ([#759](https://github.com/prebid/salesagent/issues/759)) ([690f2b1](https://github.com/prebid/salesagent/commit/690f2b12274871f3339432a23301f541f93e863e))
* display and save custom targeting keys in product inventory ([#692](https://github.com/prebid/salesagent/issues/692)) ([991656b](https://github.com/prebid/salesagent/commit/991656b31702016d744a6e1bda75674a24b4fee8))
* Docker test cleanup to prevent 100GB+ resource accumulation ([9036cae](https://github.com/prebid/salesagent/commit/9036cae83ccd3d930582cd79f11db629e8b5b4df))
* Docker test cleanup to prevent 100GB+ resource accumulation ([9ed12fd](https://github.com/prebid/salesagent/commit/9ed12fdf33ede9aed33e692894a0ea65387f2d32))
* e2e test context initialization ([0c463a1](https://github.com/prebid/salesagent/commit/0c463a16195a39ccc64ecc526856174f74382ec0))
* e2e test for media buy deliveries webhooks ([64d9529](https://github.com/prebid/salesagent/commit/64d95292edb55ff16ce993cbf20a25468fb4765e))
* edit configuration feature ([fb61f20](https://github.com/prebid/salesagent/commit/fb61f204ba66d64e5a734d81013ba0be4b5a4f7b))
* Enable all 189 integration_v2 tests - achieve 100% coverage goal ([#626](https://github.com/prebid/salesagent/issues/626)) ([6377462](https://github.com/prebid/salesagent/commit/6377462815745643b24d8c40058824261e6d863f))
* enforce brand_manifest_policy in get_products ([#731](https://github.com/prebid/salesagent/issues/731)) ([075e681](https://github.com/prebid/salesagent/commit/075e6811251861849002c557b78ab9ec251eb5d2))
* Ensure Package objects always have valid status ([#755](https://github.com/prebid/salesagent/issues/755)) ([757c0d3](https://github.com/prebid/salesagent/commit/757c0d320141c840a4861bc516b51b6263a44f0e))
* ensure User record creation during OAuth tenant selection ([#701](https://github.com/prebid/salesagent/issues/701)) ([be22ffb](https://github.com/prebid/salesagent/commit/be22ffb675032fe26610fc037b50e32620de7700))
* Exclude null values from list_authorized_properties response ([#647](https://github.com/prebid/salesagent/issues/647)) ([5afb6b5](https://github.com/prebid/salesagent/commit/5afb6b5a0544e117da8ce1a439d40a36eb0fe629))
* existing unit tests ([60a1961](https://github.com/prebid/salesagent/commit/60a1961ec1e1192d1ce85dbcabc6fadc4e409df9))
* fetch inventory by IDs to bypass 500-item API limit ([c1e197e](https://github.com/prebid/salesagent/commit/c1e197eb6d1882c317ef96b13de5d7b4dcf42418))
* fetch specific ad units by ID for placement size extraction ([85f792d](https://github.com/prebid/salesagent/commit/85f792ded5a47a2d1de0cbf351ef1eccbc31b590))
* file lint error ([#625](https://github.com/prebid/salesagent/issues/625)) ([2fec26e](https://github.com/prebid/salesagent/commit/2fec26eaf3cd51faa98100264a80d87c8c437980))
* flush deleted inventory mappings before recreating ([c83e34c](https://github.com/prebid/salesagent/commit/c83e34c8aa1712b0ec4c0f386554595f9f134255))
* GAM adapter ([f4f0df1](https://github.com/prebid/salesagent/commit/f4f0df1bc33edd4d37e1d800ba07a66df6e92c55))
* GAM adpaters and other logic changes including bumping adcp client to 2.5.5 ([8367e0a](https://github.com/prebid/salesagent/commit/8367e0a1f9d52e04ce41f81cb35bfd91c33fbcdc))
* GAM advertiser search and pagination with Select2 UI ([#710](https://github.com/prebid/salesagent/issues/710)) ([792d4ae](https://github.com/prebid/salesagent/commit/792d4ae31a27452e8043ae6b4e9baa493c9e37a5))
* GAM product placements not saving when line_item_type absent ([#691](https://github.com/prebid/salesagent/issues/691)) ([eb66e33](https://github.com/prebid/salesagent/commit/eb66e3313c9dd0fbbdfe8ff7c0b6674463e2bdd2))
* GAM test connection error fix ([78e88ae](https://github.com/prebid/salesagent/commit/78e88aeb4d05bf0ebf852a1ad2494dbc5f1c2404))
* GAM test error fix ([48b07a9](https://github.com/prebid/salesagent/commit/48b07a9c14850ca398c78b3102206b4ba09133f1))
* Handle /admin prefix in login redirects and API calls ([#733](https://github.com/prebid/salesagent/issues/733)) ([15ab582](https://github.com/prebid/salesagent/commit/15ab582e94dfdc7ed5b318bf4d2dec91b517551e))
* Handle CreateMediaBuyError response in approval and main flows ([#745](https://github.com/prebid/salesagent/issues/745)) ([574943b](https://github.com/prebid/salesagent/commit/574943b88ff076fbb0d2b9d932cde49a96e2e497))
* Handle unrestricted agents in property discovery (no property_ids = all properties) ([#750](https://github.com/prebid/salesagent/issues/750)) ([136575b](https://github.com/prebid/salesagent/commit/136575b6dcebaaea0782f9a0edf263126881daa2))
* Implement creative assignment in update_media_buy ([#560](https://github.com/prebid/salesagent/issues/560)) ([99cdcdc](https://github.com/prebid/salesagent/commit/99cdcdc741be6e103e8db3dcefa36854a63facc8))
* implement missing naming template preview logic ([39eafff](https://github.com/prebid/salesagent/commit/39eafffc6803ea51fc539c9c2bd6ed768a43aefa))
* implement missing naming template preview logic ([66fc55d](https://github.com/prebid/salesagent/commit/66fc55d0d646810454b9148128d2159f363b7d19))
* Implement missing update_media_buy field persistence ([#749](https://github.com/prebid/salesagent/issues/749)) ([f67a304](https://github.com/prebid/salesagent/commit/f67a304690067608eda74c796cf2deff4d0448d6))
* Import get_testing_context in list_authorized_properties ([#632](https://github.com/prebid/salesagent/issues/632)) ([6612c7d](https://github.com/prebid/salesagent/commit/6612c7d1870bdcf05b328452c10e44796c35a92c))
* improve creative status handling and dashboard visibility ([#711](https://github.com/prebid/salesagent/issues/711)) ([539e1bb](https://github.com/prebid/salesagent/commit/539e1bbb926c92e390a1a97529db5640b17134d0))
* improve inventory browser UX and fix search lag ([#709](https://github.com/prebid/salesagent/issues/709)) ([0d09f1b](https://github.com/prebid/salesagent/commit/0d09f1bcbc024acc13a7cdab3df2e105ec18a92a))
* include ALL statuses when fetching inventory names for existing products ([2a61600](https://github.com/prebid/salesagent/commit/2a616008f2d903c550e4d3e3e5e5c8fb5271f91d))
* Include service_account_email in adapter_config dict for template ([#517](https://github.com/prebid/salesagent/issues/517)) ([c36aef6](https://github.com/prebid/salesagent/commit/c36aef618c21720e2399dff996fa10f6f7d98bd2))
* increase sync_id length from 50 to 100 ([cd89098](https://github.com/prebid/salesagent/commit/cd890988e0ccc8d570c94c1b8addd818d075e2f2))
* increase sync_id length from 50 to 100 ([6ae87ff](https://github.com/prebid/salesagent/commit/6ae87ff9798ec4050d0ddaf96a1d75fd7a5522dd))
* Integration tests, mypy errors, and AdCP schema compliance ([#633](https://github.com/prebid/salesagent/issues/633)) ([77c4da6](https://github.com/prebid/salesagent/commit/77c4da632b35b806452b89bdafd1bce781699fff))
* Integration tests, mypy errors, and deprecation warnings ([#628](https://github.com/prebid/salesagent/issues/628)) ([be52151](https://github.com/prebid/salesagent/commit/be521514a146ae765c879f7ad3b84d4c9358462e))
* Integration tests, mypy errors, and test infrastructure improvements ([#631](https://github.com/prebid/salesagent/issues/631)) ([ca4c184](https://github.com/prebid/salesagent/commit/ca4c1846d38a95442d1ec7d89710a2a8ffdf5d6d))
* inventory profile save URL and property_mode handling ([40f192a](https://github.com/prebid/salesagent/commit/40f192a8351143d3d92f63a62944032ab0019ac9))
* inventory profile save URL and property_mode handling ([7440350](https://github.com/prebid/salesagent/commit/7440350d323443e3e7a16dc1149b0b19ec1b0f34))
* inventory sync ([d300258](https://github.com/prebid/salesagent/commit/d300258260bd64f7aaaf75f0d1c359380783f153))
* Inventory sync JavaScript errors ([0d2ad1f](https://github.com/prebid/salesagent/commit/0d2ad1ff915a30849534eaf66318518166a49edc))
* inventory sync status now checks GAMInventory table instead of Products ([#708](https://github.com/prebid/salesagent/issues/708)) ([193e87d](https://github.com/prebid/salesagent/commit/193e87d0cf3c4ca0cab1d5edc16911a0def1711b))
* lint errors ([dff427a](https://github.com/prebid/salesagent/commit/dff427a546a52933b9d9a05899b8ccd1abfa3fc6))
* list_tasks query using non-existent WorkflowStep.tenant_id ([#822](https://github.com/prebid/salesagent/issues/822)) ([c17abcb](https://github.com/prebid/salesagent/commit/c17abcb1d2d6a91577f2cf99f4df690131670f8b))
* Load pricing_options when querying products ([#413](https://github.com/prebid/salesagent/issues/413)) ([a87c69a](https://github.com/prebid/salesagent/commit/a87c69aee9568835cd599d3de7754f6c632c696e))
* make media_buy_ids optional in get_media_buy_delivery per AdCP spec ([#704](https://github.com/prebid/salesagent/issues/704)) ([5c69013](https://github.com/prebid/salesagent/commit/5c690131d9d90a59acc47e10954768adf9456cff))
* media buy tests creation ([4045386](https://github.com/prebid/salesagent/commit/4045386a4e5f498f087219197dfc9a266e5176be))
* media buys & creatives ([58c4f45](https://github.com/prebid/salesagent/commit/58c4f45901abfaa3458336c23ec69e5c569efe7d))
* mypy ([77b5ecc](https://github.com/prebid/salesagent/commit/77b5ecc2fd215ba7761dcd9437f1049a497ca3ac))
* nest inventory picker modal to resolve search input focus issue ([a14c47b](https://github.com/prebid/salesagent/commit/a14c47b835252303339eb3d4ca4c2da1060c2e99))
* nest inventory picker modal to resolve search input focus issue ([f888fe9](https://github.com/prebid/salesagent/commit/f888fe93ad6cd7b733e663bb7414204ff9e835d3))
* Normalize agent URL variations for consistent validation ([#497](https://github.com/prebid/salesagent/issues/497)) ([9bef942](https://github.com/prebid/salesagent/commit/9bef94207b271f9436347536c1df4dc5ba9f0f8c))
* parse and apply custom targeting from product forms to GAM line items ([#686](https://github.com/prebid/salesagent/issues/686)) ([a1132ae](https://github.com/prebid/salesagent/commit/a1132aef30c7bdf8fb1ceefee8721217c4f31aef))
* pass DELIVERY_WEBhOOK_INTERVAL when running e2e tests in ci/cd ([07f3eee](https://github.com/prebid/salesagent/commit/07f3eee4ee8b2baa67c1cb55c63df014c7fad1be))
* persist targeting and placement selections in product editor ([#689](https://github.com/prebid/salesagent/issues/689)) ([ebbecf0](https://github.com/prebid/salesagent/commit/ebbecf047e56b3ea6004d5721f23421b029c4363))
* populate custom targeting keys when editing products ([#693](https://github.com/prebid/salesagent/issues/693)) ([88f0b9e](https://github.com/prebid/salesagent/commit/88f0b9ea6df0f1507638d7f46674e7c1dd7b3f45))
* prevent duplicate IDs in placement display after removal ([#696](https://github.com/prebid/salesagent/issues/696)) ([87b0eac](https://github.com/prebid/salesagent/commit/87b0eac31f4f2b788f6c01e4ad6887a2fa30fcf3))
* Prevent duplicate tenant display when user has both domain and email access ([#660](https://github.com/prebid/salesagent/issues/660)) ([92ca049](https://github.com/prebid/salesagent/commit/92ca049e0d34c77d0473430f50129bbbaedc2553))
* product editor bugs - JSON parsing, text color, selection preservation ([#694](https://github.com/prebid/salesagent/issues/694)) ([50765cf](https://github.com/prebid/salesagent/commit/50765cfd83b581a4e7141dd7e837e6a57ff48bae))
* rebase ([581b18b](https://github.com/prebid/salesagent/commit/581b18b4a49bc811329534dcde1f0d3b81ce2f76))
* Reduce skipped tests from 323 to ~97 (70% improvement) ([#669](https://github.com/prebid/salesagent/issues/669)) ([c48f978](https://github.com/prebid/salesagent/commit/c48f978f427d17b3092261d67d823fff18093d61))
* rejection ([79cb754](https://github.com/prebid/salesagent/commit/79cb754c6240dd8370a73642bdf8f6caa5f5aca8))
* remove /a2a suffix from A2A endpoint URLs and add name field to configs ([2b036c6](https://github.com/prebid/salesagent/commit/2b036c6fc44a3316d15e82c0245d70d447b7142c))
* remove /a2a suffix from A2A endpoint URLs and add name field to configs ([13914b8](https://github.com/prebid/salesagent/commit/13914b8584dea3d17c8e751ad7d7db58c2b3e2b2))
* remove 97% of type: ignore comments and fix 169 mypy errors ([#820](https://github.com/prebid/salesagent/issues/820)) ([#823](https://github.com/prebid/salesagent/issues/823)) ([1175c63](https://github.com/prebid/salesagent/commit/1175c631a833fcd1f888bfc98e8949cecad6ece9))
* Remove auto-restart of delivery simulators on server boot ([#646](https://github.com/prebid/salesagent/issues/646)) ([52c2378](https://github.com/prebid/salesagent/commit/52c2378d20620a2ab55f125d6a0f87ead73ccb02))
* remove dead API docs link and fix testing docs path ([#700](https://github.com/prebid/salesagent/issues/700)) ([9fd959e](https://github.com/prebid/salesagent/commit/9fd959eed4c98a9d6ddb7f3fbb5abbba02cc99a7)), closes [#676](https://github.com/prebid/salesagent/issues/676)
* Remove fake media_buy_id from pending/async responses in mock adapter ([#658](https://github.com/prebid/salesagent/issues/658)) ([dc2a2ba](https://github.com/prebid/salesagent/commit/dc2a2ba63dca42e36f0d6b6cae6a9d23c22468cb))
* remove inventory sync requirement for mock adapter ([#719](https://github.com/prebid/salesagent/issues/719)) ([4268b2e](https://github.com/prebid/salesagent/commit/4268b2e9a93a499ec6b03518b8c3c3fd42361568))
* Remove non-existent fields from SyncCreativesResponse ([9bf3da7](https://github.com/prebid/salesagent/commit/9bf3da7b358d55739e9687d50b0a62f0a7d5ce22))
* Remove non-existent fields from SyncCreativesResponse ([453c329](https://github.com/prebid/salesagent/commit/453c329b40899fdcaea9bffc1fc766875a1b963b))
* Remove non-existent impressions field from AdCPPackageUpdate ([#500](https://github.com/prebid/salesagent/issues/500)) ([404c653](https://github.com/prebid/salesagent/commit/404c6539b7a915b1df47ea797bd181c70aac6312))
* Remove non-spec tags field from ListAuthorizedPropertiesResponse ([#643](https://github.com/prebid/salesagent/issues/643)) ([a38b3d7](https://github.com/prebid/salesagent/commit/a38b3d751ecb3bf55983020ec52d08a4fc20053c))
* Remove stale ui-test-assistant MCP server configuration ([#851](https://github.com/prebid/salesagent/issues/851)) ([0e7cf9a](https://github.com/prebid/salesagent/commit/0e7cf9aba2879fe336ff8f1c7f4872e1e70c9f6d))
* remove top-level budget requirement from create_media_buy ([#725](https://github.com/prebid/salesagent/issues/725)) ([4474de3](https://github.com/prebid/salesagent/commit/4474de3d1cf724c6dddc6b0bc77c999015e1acd3))
* Replace progress_data with progress in SyncJob ([f4008f4](https://github.com/prebid/salesagent/commit/f4008f430fddc6acb1822ac9c68875e17bc5c99c))
* require authentication for sync_creatives and update_media_buy ([#721](https://github.com/prebid/salesagent/issues/721)) ([defa383](https://github.com/prebid/salesagent/commit/defa3837a52bede3635a3d1d3f74eb0e84c37972))
* Resolve GAM inventory sync and targeting data loading issues ([#675](https://github.com/prebid/salesagent/issues/675)) ([ca31c6a](https://github.com/prebid/salesagent/commit/ca31c6a6334d0db9afa3beadefdfb5d77429f503))
* Resolve product creation and format URL issues ([#756](https://github.com/prebid/salesagent/issues/756)) ([d99a6f8](https://github.com/prebid/salesagent/commit/d99a6f83416d39864e43193eb5db07a4e6595463))
* Restore accidentally deleted commitizen configuration files ([c92075c](https://github.com/prebid/salesagent/commit/c92075c8c9d2602484cb3153fdbbd5460e4fa0f2))
* Restore brand manifest policy migrations and merge with signals agent ([e30c106](https://github.com/prebid/salesagent/commit/e30c106c9517fa342a06ca0ace829b63780532a9))
* restore unrelative changes ([8c159e6](https://github.com/prebid/salesagent/commit/8c159e659ac7f01e252de4ee8c44654718add4e6))
* Return human-readable text in MCP protocol messages ([#644](https://github.com/prebid/salesagent/issues/644)) ([3bb9bce](https://github.com/prebid/salesagent/commit/3bb9bcedef3d9d19e3564f76847468ced02bf812))
* Route external domains to tenant login instead of signup ([#661](https://github.com/prebid/salesagent/issues/661)) ([b194b83](https://github.com/prebid/salesagent/commit/b194b83757250efce28f07da7496ef681a18a73f))
* sales agent logic ([0a51476](https://github.com/prebid/salesagent/commit/0a51476a9411f7f31d7daa495322b071bda91ca3))
* sanitize tenant ID in GCP service account creation ([b4c3bbc](https://github.com/prebid/salesagent/commit/b4c3bbc7b221d93a5f9ad5fa6495f9ae82dba338))
* sanitize tenant ID in GCP service account creation ([6774587](https://github.com/prebid/salesagent/commit/67745871f9c52700de3ad522ce45e6a415c31e5c))
* Set session role for super admin OAuth login ([#654](https://github.com/prebid/salesagent/issues/654)) ([505b24f](https://github.com/prebid/salesagent/commit/505b24f45a2d9cf573e8726ea011f51cba7a1c27))
* set tenant context before fetching delivery metrics ([1042274](https://github.com/prebid/salesagent/commit/10422746dc85d063615b6a1c67cc96a31734866e))
* Set tenant context when x-adcp-tenant header provides direct tenant_id ([#467](https://github.com/prebid/salesagent/issues/467)) ([20b3f9c](https://github.com/prebid/salesagent/commit/20b3f9c88171643ed8e8f0117029fb94eb63ff41))
* show both name and ID for placements consistently ([#695](https://github.com/prebid/salesagent/issues/695)) ([52caddd](https://github.com/prebid/salesagent/commit/52caddd69f0785fd9cd2a8b7d1c9e742c3766f47))
* signals agent test endpoint async handling ([#718](https://github.com/prebid/salesagent/issues/718)) ([e1c5d72](https://github.com/prebid/salesagent/commit/e1c5d722db002c22d16ad28f6f272b2aafa08359))
* Support ListCreativesRequest convenience fields with adcp 2.9.0 ([#770](https://github.com/prebid/salesagent/issues/770)) ([1bd57f0](https://github.com/prebid/salesagent/commit/1bd57f0fd8179c6fb7eacfea079da60ae06752d7))
* syntax ([af504a6](https://github.com/prebid/salesagent/commit/af504a690ad2ad4da7a660308a089869969a97f6))
* Targeting browser, product page auth, UI repositioning + format conversion tests ([#683](https://github.com/prebid/salesagent/issues/683)) ([d363627](https://github.com/prebid/salesagent/commit/d3636275cbf5b1ac2aae50fa91639b221993a38c))
* targeting keys errors in browser and product pages ([#685](https://github.com/prebid/salesagent/issues/685)) ([7fc3603](https://github.com/prebid/salesagent/commit/7fc3603c63f9d0a870b5b36fd86763bcb277dfb7))
* test ([62c2fe0](https://github.com/prebid/salesagent/commit/62c2fe0bca0fd7416770689929986385f10d52a2))
* test delivery webhook sends for fresh data ([b35457f](https://github.com/prebid/salesagent/commit/b35457f4746eac673d5c64f4d4f7a3fa10501262))
* test scase in test_format_conversion_approval ([3060a24](https://github.com/prebid/salesagent/commit/3060a243664408ee26ef2cb4fcd90638022f3389))
* tests ([5d5347c](https://github.com/prebid/salesagent/commit/5d5347ce8502893a606bfa3778c8ee6d4e541a77))
* tests ([1b1ce8e](https://github.com/prebid/salesagent/commit/1b1ce8e3f29ef0592efe09692ac98e06cdd6c8fb))
* tests ([f70f684](https://github.com/prebid/salesagent/commit/f70f6845447bd920fed134d68d736fa1f818b131))
* tests ([c966e43](https://github.com/prebid/salesagent/commit/c966e43d21987bae837bb5eac19c52ee95122f54))
* try to pass delivery interval through docker-compose.override.yml for e2e tests ([c830255](https://github.com/prebid/salesagent/commit/c83025543430ebefab6260b8000a57c8f7cd39fd))
* types ([d545f14](https://github.com/prebid/salesagent/commit/d545f14bf8cb165b8de0617f24360571aceff09a))
* typo in integration test ([d09125e](https://github.com/prebid/salesagent/commit/d09125eeec4c5e39c8010b67a781162d37f727a3))
* Unskip 3 integration tests and reduce mypy errors by 330 ([#627](https://github.com/prebid/salesagent/issues/627)) ([37cc165](https://github.com/prebid/salesagent/commit/37cc1656a3ffd192dd127d68aff7cc1194b86bed))
* Update DNS widget to use A record pointing to Approximated proxy IP ([#636](https://github.com/prebid/salesagent/issues/636)) ([3291ae6](https://github.com/prebid/salesagent/commit/3291ae684174cc8d2d6de4188a384fc18b9ddeb2))
* Update tenant selector template to work with dictionary objects ([#652](https://github.com/prebid/salesagent/issues/652)) ([aa612a3](https://github.com/prebid/salesagent/commit/aa612a35aae011f638ed906ac2c71b0a50d3757d))
* Use content-based hashing for schema sync to avoid metadata noise ([#649](https://github.com/prebid/salesagent/issues/649)) ([5625955](https://github.com/prebid/salesagent/commit/5625955d913bb6ea4264c04d0ba9d4767f9a57fd))
* use correct field name inventory_metadata in IDs path ([4e7d7a2](https://github.com/prebid/salesagent/commit/4e7d7a2344d2553e9396ff53de7031fcf7e9873b))
* Use SQLAlchemy event listener for statement_timeout with PgBouncer ([#641](https://github.com/prebid/salesagent/issues/641)) ([bde8186](https://github.com/prebid/salesagent/commit/bde8186e1d182cd0279b1e0c772fb79fa09654ea))
* wrap service account credentials with GoogleCredentialsClient ([#727](https://github.com/prebid/salesagent/issues/727)) ([9d21709](https://github.com/prebid/salesagent/commit/9d2170948c9efd844b4f1a7ef658935860947351))


### Documentation

* clarify GAM setup with three clear paths and environment validation ([#847](https://github.com/prebid/salesagent/issues/847)) ([6a2e951](https://github.com/prebid/salesagent/commit/6a2e95143bc736795df1bd83a87e421024d182d3))
* document PYTHONPATH requirement for Docker hot reload ([#846](https://github.com/prebid/salesagent/issues/846)) ([03878f4](https://github.com/prebid/salesagent/commit/03878f46de5132430e561f032edbd7070d3dbe5c))

## [Unreleased]

### Added
- Changeset system for automated version management
- CI workflows to enforce changeset requirements on PRs
- Automated version bump PR creation when changesets are merged

## [0.1.0] - 2025-01-29

Initial release of the Prebid Sales Agent reference implementation.

### Added
- MCP server implementation with AdCP v2.3 support
- A2A (Agent-to-Agent) protocol support
- Multi-tenant architecture with PostgreSQL
- Google Ad Manager (GAM) adapter
- Mock ad server adapter for testing
- Admin UI with Google OAuth authentication
- Comprehensive testing backend with dry-run support
- Real-time activity dashboard with SSE
- Workflow management system
- Creative management and approval workflows
- Audit logging
- Docker deployment support
- Extensive documentation

[Unreleased]: https://github.com/prebid/salesagent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/prebid/salesagent/releases/tag/v0.1.0

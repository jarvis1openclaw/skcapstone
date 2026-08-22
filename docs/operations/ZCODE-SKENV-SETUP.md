# ZCode and SKEnv setup

This host runs ZCode with the Z.AI Coding Plan account connection and the
SKCapstone MCP registry. The local ZCode CLI configuration is kept outside
this repository at `~/.zcode/cli/config.json`.

## Permission mode

ZCode's supported unrestricted execution mode is named `yolo`. The host
configuration sets:

```json
{
  "mode": "yolo"
}
```

`SK_ZCODE_YOLO=1` is an SKEnv intent marker. It is not a ZCode authorization
switch. ZCode's own `mode` setting controls its tool approval behavior.

CapAuth, SKLegal policy mediation, matter isolation, and external-action gates
remain in force. Full access does not authorize filing, service, messaging,
production, or other external actions.

## Verification

Use the ZCode CLI runtime to inspect the supported mode:

```bash
node /opt/ZCode/resources/glm/zcode.cjs --help
```

The help output must list `yolo` among the supported permission modes. A
desktop ZCode session should be restarted after changing the user config. The
interactive session also exposes `/mode yolo`. The standalone CLI requires an
explicit model-provider configuration; the desktop client supplies the
authenticated built-in provider. Neither path bypasses SKLegal policy or
CapAuth controls.

## Rollback

Remove the top-level `mode` entry from `~/.zcode/cli/config.json`, unset
`ZCODE_MODE`, and start a new ZCode session. The repository documentation can
remain as historical setup evidence.

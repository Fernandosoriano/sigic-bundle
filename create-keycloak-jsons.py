# -*- coding: utf-8 -*-
import json
import os
import re
import sys


def parse_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def build_replacements(env):
    return {
        "nginxbaseurl": env["NGINX_BASE_URL"],

        "kcadm_cid": env["ADMIN_KEYCLOAK_CLIENT_ID"],
        "kcadm_secret": env["ADMIN_KEYCLOAK_CLIENT_SECRET"],

        "kcapp_cid": env["APP_KEYCLOAK_CLIENT_ID"],
        "kcapp_secret": env["APP_KEYCLOAK_CLIENT_SECRET"],

        "kcgn_cid": env["SOCIALACCOUNT_OIDC_CLIENT_ID"],
        "kcgn_secret": env["SOCIALACCOUNT_OIDC_CLIENT_SECRET"],
    }


def render(content, values):
    for k, v in values.items():
        content = re.sub(r"\{" + re.escape(k) + r"\}", v, content)
    return content


# 🔥 VALIDACIÓN REAL
def validate_client(data, path):
    errors = []

    if not data.get("clientId"):
        errors.append("clientId faltante")

    if data.get("clientAuthenticatorType") == "client-secret":
        if not data.get("secret"):
            errors.append("secret faltante")

    # URLs
    for field in ["rootUrl", "adminUrl", "baseUrl"]:
        val = data.get(field)
        if val is not None:
            if "{" in val:
                errors.append(f"{field} contiene placeholders sin resolver")
            if not val.startswith("http"):
                errors.append(f"{field} no parece URL válida: {val}")

    # redirectUris
    for uri in data.get("redirectUris", []):
        if "{" in uri:
            errors.append(f"redirectUri sin resolver: {uri}")
        if not uri.startswith("http"):
            errors.append(f"redirectUri inválida: {uri}")

    if errors:
        raise Exception(f"Errores en {path}:\n - " + "\n - ".join(errors))


def process_file(path, values, output_dir=None):
    with open(path) as f:
        content = f.read()

    rendered = render(content, values)

    # validar JSON
    data = json.loads(rendered)

    # validar estructura
    validate_client(data, path)

    if output_dir:
        filename = os.path.basename(path).replace(".template", "")
        out = os.path.join(output_dir, filename)
    else:
        out = path.replace(".template", "")

    with open(out, "w") as f:
        json.dump(data, f, indent=2)

    print(f"OK: {out}")


def main():
    base_dir = os.path.dirname(os.path.realpath(__file__))
    env_path = os.path.join(base_dir, ".env")
    templates_dir = os.path.join(base_dir, "overrides", "keycloak")

    output_dir = os.environ.get("KC_JSON_OUTPUT_DIR")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    env = parse_env(env_path)
    values = build_replacements(env)

    for name in os.listdir(templates_dir):
        if name.endswith(".json.template"):
            process_file(os.path.join(templates_dir, name), values, output_dir)


if __name__ == "__main__":
    main()
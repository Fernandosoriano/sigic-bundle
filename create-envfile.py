# -*- coding: utf-8 -*-
#########################################################################
#
# Copyright (C) 2022 OSGeo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################
import argparse
import ast
import json
import logging
import os
import random
import re
import secrets
import string
import sys

dir_path = os.path.dirname(os.path.realpath(__file__))

logger = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


def shuffle(chars):
    chars_as_list = list(chars)
    random.shuffle(chars_as_list)
    return "".join(chars_as_list)


_simple_chars = shuffle(string.ascii_letters + string.digits)
_strong_chars = shuffle(
    string.ascii_letters
    + string.digits
    + string.punctuation.replace('"', "").replace("'", "").replace("`", "")
    .replace("#", "").replace("\\", "").replace("$", "").replace("=", "")
)


def generate_env_file(args):
    # validity checks
    if not os.path.exists(args.sample_file):
        logger.error(f"File does not exists {args.sample_file}")
        raise FileNotFoundError

    if args.file and not os.path.isfile(args.file):
        logger.error(f"File does not exists: {args.file}")
        raise FileNotFoundError

    if args.https and not args.email:
        raise Exception("With HTTPS enabled, the email parameter is required")

    _sample_file = None
    with open(args.sample_file, "r+") as sample_file:
        _sample_file = sample_file.read()

    if not _sample_file:
        raise Exception("Sample file is empty!")

    def _get_vals_to_replace(args):
        _config = ["sample_file", "file", "env_type", "https", "email"]
        _jsfile = {}
        if args.file:
            with open(args.file) as _json_file:
                _jsfile = json.load(_json_file)

        _vals_to_replace = {
            key: _jsfile.get(key, val)
            for key, val in vars(args).items()
            if key not in _config
        }
        tcp = (
            "https"
            if ast.literal_eval(f"{_jsfile.get('https', args.https)}".capitalize())
            else "http"
        )

        useoidc = _jsfile.get("useoidc", args.useoidc)
        _vals_to_replace["useoidc"] = (
            True if useoidc else False
        )

        usefrontendadmin = _jsfile.get("usefrontendadmin", args.usefrontendadmin)
        _vals_to_replace["usefrontendadmin"] = (
            True if usefrontendadmin else False
        )

        usefrontendapp = _jsfile.get("usefrontendapp", args.usefrontendapp)
        _vals_to_replace["usefrontendapp"] = (
            True if usefrontendapp else False
        )

        enableiaproxy = _jsfile.get("enableiaproxy", args.enableiaproxy)
        _vals_to_replace["enableiaproxy"] = (
            True if enableiaproxy else False
        )

        enablelevantamientoproxy = _jsfile.get("enablelevantamientoproxy", args.enablelevantamientoproxy)
        _vals_to_replace["enablelevantamientoproxy"] = (
            "" if enablelevantamientoproxy else "# "
        )

        enableiadb = _jsfile.get("enableiadb", args.enableiadb)
        _vals_to_replace["enableiadb"] = (
            True if enableiadb else False
        )

        enablelevantamientodb = _jsfile.get("enablelevantamientodb", args.enablelevantamientodb)
        _vals_to_replace["enablelevantamientodb"] = (
            True if enablelevantamientodb else False
        )

        oidc_provider_url = _jsfile.get("oidc_provider_url", args.oidc_provider_url)
        _vals_to_replace["oidc_enabled"] = (
            True if oidc_provider_url and oidc_provider_url != "" else False
        )
        _vals_to_replace["oidc_provider_url"] = (
            oidc_provider_url if oidc_provider_url else "https://iam.dev.sigic.mx/realms/sigic"
        )

        _vals_to_replace["http_host"] = _jsfile.get("hostname", args.hostname)
        _vals_to_replace["https_host"] = (
            _jsfile.get("hostname", args.hostname) if tcp == "https" else ""
        )

        subpath = "/geonode" if args.use_subpath else ""
        _vals_to_replace["subpath"] = subpath
        _vals_to_replace["FORCE_SCRIPT_NAME"] = subpath

        _vals_to_replace["STATIC_URL"] = (
            f"{subpath}/static/" if subpath else "/static/"
        )

        nginxproto = "https" if args.externalhttps else tcp

        _vals_to_replace["http_scheme"] = nginxproto

        _vals_to_replace["siteurl"] = f"{nginxproto}://{_jsfile.get('hostname', args.hostname)}{subpath}"
        _vals_to_replace["nginxbaseurl"] = f"{nginxproto}://{_jsfile.get('hostname', args.hostname)}"

        _vals_to_replace["secret_key"] = _jsfile.get(
            "secret_key", args.secret_key
        ) or secrets.token_urlsafe(50)
        _vals_to_replace["letsencrypt_mode"] = (
            "disabled"
            if not _vals_to_replace.get("https_host")
            else "staging"
            if _jsfile.get("env_type", args.env_type) in ["test"]
            else "production"
        )
        _vals_to_replace["debug"] = (
            False
            if _jsfile.get("env_type", args.env_type) in ["prod", "test"]
            else True
        )
        _vals_to_replace["email"] = _jsfile.get("email", args.email)
        _vals_to_replace["homepath"] = _jsfile.get("homepath", args.homepath) if args.homepath else "app"

        if tcp == "https" and not _vals_to_replace["email"]:
            raise Exception("With HTTPS enabled, the email parameter is required")

        _vals_to_replace["ia_django_secret_key"] = secrets.token_urlsafe(50)

        return {**_jsfile, **_vals_to_replace}

    for key, val in _get_vals_to_replace(args).items():
        if key in ["subpath", "homepath", "enablelevantamientoproxy"]:
            _val = "" if not val else str(val)
        else:
            _val = val or "".join(random.choice(_simple_chars) for _ in range(15))
        if isinstance(val, bool) or key in ["email", "http_host", "https_host"]:
            _val = str(val)
        _sample_file = re.sub(
            "{" + key + "}",
            lambda _: _val,
            _sample_file,
        )

    with open(f"{dir_path}/.env", "w+") as output_env:
        output_env.write(_sample_file)
    logger.info(f".env file created: {dir_path}/.env")

    # 👇 ejecutar generación de JSONs de Keycloak automáticamente
    try:
        import subprocess

        script_path = os.path.join(dir_path, "create-keycloak-jsons.py")

        if os.path.isfile(script_path):
            subprocess.run(
                [sys.executable, script_path],
                check=True,
            )
            logger.info("Keycloak JSONs generated successfully")
        else:
            logger.warning(f"No se encontró create-keycloak-jsons.py en {dir_path}")

    except Exception as e:
        logger.error(f"Error generating Keycloak JSONs: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="ENV file builder",
        description=(
            "Tool for generate environment file automatically. "
            "The information can be passed or via CLI or via JSON file ( --file /path/env.json)"
        ),
        usage="python create-envfile.py localhost -f /path/to/json/file.json",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--noinput",
        "--no-input",
        action="store_false",
        dest="confirmation",
        help="skips prompting for confirmation.",
    )
    parser.add_argument(
        "-hn",
        "--hostname",
        help="Host name, default localhost",
        default="localhost",
    )

    # expected path as a value
    parser.add_argument(
        "-sf",
        "--sample_file",
        help=f"Path of the sample file to use as a template. Default is: {dir_path}/.env.sample",
        default=f"{dir_path}/.env.sample",
    )
    parser.add_argument(
        "-f",
        "--file",
        help=(
            "absolute path of the file with the configuration. "
            "Note: we expect that the keys of the dictionary have the same name as the CLI params"
        ),
    )
    # booleans
    parser.add_argument(
        "--https", action="store_true", default=False, help="If provided, https is used"
    )
    # strings
    parser.add_argument(
        "--email", help="Admin email, this field is required if https is enabled"
    )
    parser.add_argument(
        "--homepath", help="Default home path"
    )

    parser.add_argument("--geonodepwd", help="GeoNode admin password")
    parser.add_argument("--geoserverpwd", help="Geoserver admin password")
    parser.add_argument("--levantamientodbpwd", help="Geoserver admin password")
    parser.add_argument("--pgpwd", help="PostgreSQL password")
    parser.add_argument("--dbpwd", help="GeoNode DB user password")
    parser.add_argument("--geodbpwd", help="Geodatabase user password")
    parser.add_argument("--clientid", help="Oauth2 client id")
    parser.add_argument("--clientsecret", help="Oauth2 client secret")
    parser.add_argument("--secret_key", help="Django Secret Key")

    parser.add_argument(
        "--env_type",
        help="Development/production or test",
        choices=["prod", "test", "dev"],
        default="prod",
    )
    parser.add_argument(
        "--use_subpath",
        action="store_true",
        help="If provided, it indicates that GeoNode runs under a subpath.",
    )
    parser.add_argument(
        "--oidc_provider_url",
        help="OIDC Provider URL",
        default=None,
    )
    parser.add_argument(
        "--useoidc", action="store_true", default=False, help="If provided, bundled keycloak is used"
    )

    parser.add_argument(
        "--usefrontendadmin", action="store_true", default=False, help="If provided, bundled frontend admin is used"
    )

    parser.add_argument(
        "--usefrontendapp", action="store_true", default=False, help="If provided, bundled frontend app is used"
    )
    parser.add_argument(
        "--enableiaproxy", action="store_true", default=False, help="If provided, bundled ia proxy is used"
    )
    parser.add_argument(
        "--enablelevantamientoproxy", action="store_true", default=False, help="If provided, bundled levantamiento proxy is used"
    )
    parser.add_argument(
        "--enableiadb", action="store_true", default=False, help="If provided, bundled ia db is used"
    )
    parser.add_argument(
        "--enablelevantamientodb", action="store_true", default=False, help="If provided, bundled levantamiento db is used"
    )
    parser.add_argument(
        "--externalhttps", action="store_true", default=False, help="If provided, external https is used"
    )

    parser.add_argument("--kcadm_cid", help="Keycloak admin client id")
    parser.add_argument("--kcadm_secret", help="Keycloak admin client secret")
    parser.add_argument("--kcgn_cid", help="Keycloak geonode client id")
    parser.add_argument("--kcgn_secret", help="Keycloak geonode client secret")
    parser.add_argument("--kcapp_cid", help="Keycloak public app client id")
    parser.add_argument("--kcapp_secret", help="Keycloak public app client secret")
    parser.add_argument("--kckadm_password", help="Keycloak kadmin password")
    parser.add_argument("--adm_nuxt_auth_secret", help="Nuxt admin auth secret")
    parser.add_argument("--app_nuxt_auth_secret", help="Nuxt public auth secret")
    parser.add_argument("--ia_db_password", help="IA engine database password")

    args = parser.parse_args()

    env_path = os.path.join(dir_path, ".env")

    # si no existe .env → generar sin preguntar
    if not os.path.exists(env_path):
        generate_env_file(args)

    # si existe y se pasó --noinput → sobrescribir sin preguntar
    elif not args.confirmation:
        generate_env_file(args)

    # si existe → preguntar
    else:
        overwrite_env = input(
            "Esta acción puede sobreescribir el archivo .env existente. Deseas sobreescribirlo? (y/N)"
        ).strip().lower()

        # ENTER vacío = "n"
        if overwrite_env == "y":
            generate_env_file(args)
        else:
            logger.info("Se conserva el .env existente")

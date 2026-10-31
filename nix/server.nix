{
  lib,
  makeWrapper,
  python312Packages,
  git,
  openssh,
  ripgrep,
  frontend,
}:

python312Packages.buildPythonApplication {
  pname = "second-brain";
  version = "0.1.0";
  pyproject = true;
  src = lib.cleanSource ../.;

  build-system = [ python312Packages.hatchling ];
  dependencies = with python312Packages; [
    apscheduler
    bcrypt
    click
    fastapi
    httpx
    itsdangerous
    jinja2
    pydantic
    pydantic-settings
    python-frontmatter
    python-multipart
    python-ulid
    pyyaml
    uvicorn
    zstandard
  ];

  nativeBuildInputs = [ makeWrapper ];

  postInstall = ''
    sitePackages="$(find "$out" -type d -path '*/site-packages' -print -quit)"
    test -n "$sitePackages"
    static="$sitePackages/app/static"
    mkdir -p "$static"
    cp -r ${frontend}/dist/. "$static/"
    wrapProgram "$out/bin/second-brain" \
      --prefix PATH : ${lib.makeBinPath [ git openssh ripgrep ]}
  '';

  # The backend test suite needs a configured vault/LLM server. It is exercised
  # separately by the project's development commands rather than at package build time.
  doCheck = false;

  meta = {
    description = "Self-hosted personal second brain";
    homepage = "https://github.com/Mickael-Roger/second-brain";
    mainProgram = "second-brain";
    platforms = lib.platforms.unix;
  };
}

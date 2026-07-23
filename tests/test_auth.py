from white_soapstone.drive import auth


def test_get_credentials_fires_on_auth_required_before_blocking_on_sign_in(monkeypatch):
    monkeypatch.setattr(auth, "_load_cached_credentials", lambda: None)
    monkeypatch.setattr(auth.Path, "exists", lambda self: True)

    calls = []

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            return cls()

        def run_local_server(self, **kwargs):
            calls.append("run_local_server")
            return "fake-creds"

    monkeypatch.setattr(auth, "InstalledAppFlow", FakeFlow)
    monkeypatch.setattr(auth, "_store_credentials", lambda creds: None)

    fired = []
    creds = auth.get_credentials("client_secret.json", on_auth_required=lambda: fired.append(True))

    assert fired == [True]
    assert calls == ["run_local_server"]
    assert creds == "fake-creds"


def test_get_credentials_skips_auth_callback_when_cached_creds_are_valid(monkeypatch):
    class FakeCreds:
        valid = True

    monkeypatch.setattr(auth, "_load_cached_credentials", lambda: FakeCreds())

    fired = []
    creds = auth.get_credentials("client_secret.json", on_auth_required=lambda: fired.append(True))

    assert fired == []
    assert isinstance(creds, FakeCreds)

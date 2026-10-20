from auditer.utils.security import (
    contains_token,
    mask_token,
    sanitise_git_output,
    sanitise_url,
)


class TestMaskToken:
    def test_none_token(self):
        assert mask_token(None) == "(none)"

    def test_empty_token(self):
        assert mask_token("") == "(none)"

    def test_short_token(self):
        assert mask_token("abc") == "****"

    def test_normal_token(self):
        result = mask_token("ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234")
        assert result == "****1234"
        assert "ghp_" not in result

    def test_shows_last_4(self):
        result = mask_token("ghp_abcdefghijklmnopqrstuvwxyz1234567890ABCD")
        assert result.endswith("ABCD")


class TestSanitiseUrl:
    def test_clean_url_unchanged(self):
        url = "https://github.com/user/repo.git"
        assert sanitise_url(url) == url

    def test_token_in_url_masked(self):
        url = "https://oauth2:ghp_secret123456@github.com/user/repo.git"
        result = sanitise_url(url)
        assert "ghp_secret" not in result
        assert "****@github.com" in result

    def test_http_url_masked(self):
        url = "http://oauth2:token@github.com/user/repo.git"
        result = sanitise_url(url)
        assert "token" not in result


class TestSanitiseGitOutput:
    def test_clean_output_unchanged(self):
        text = "Cloning into 'my-repo'..."
        assert sanitise_git_output(text) == text

    def test_ghp_token_masked(self):
        text = "remote: Invalid credentials ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitise_git_output(text)
        assert "ghp_abcdef" not in result
        assert "****" in result

    def test_github_pat_token_masked(self):
        token = "github_pat_" + "A" * 82
        text = f"Using token {token}"
        result = sanitise_git_output(text)
        assert "github_pat_" not in result

    def test_gho_token_masked(self):
        token = "gho_" + "A" * 40
        text = f"Token: {token}"
        result = sanitise_git_output(text)
        assert "gho_" not in result

    def test_ghs_token_masked(self):
        token = "ghs_" + "A" * 40
        text = f"Token: {token}"
        result = sanitise_git_output(text)
        assert "ghs_" not in result

    def test_url_with_token_masked(self):
        text = "fatal: unable to access 'https://oauth2:ghp_secret@github.com/user/repo.git'"
        result = sanitise_git_output(text)
        assert "ghp_secret" not in result


class TestContainsToken:
    def test_no_token(self):
        assert not contains_token("just some normal text")

    def test_ghp_token(self):
        assert contains_token("ghp_" + "a" * 36)

    def test_github_pat_token(self):
        assert contains_token("github_pat_" + "A" * 82)

    def test_gho_token(self):
        assert contains_token("gho_" + "A" * 36)

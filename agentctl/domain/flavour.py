"""Flavours: the CLI a persona runs, and everything that differs with it. Adding a CLI is one more class.

Each Flavour knows its command, the .env variables that pin and tag its image (CLAUDE_VERSION /
CLAUDE_TAG, GROK_VERSION / GROK_TAG), the release channels its upgrade understands (the first is the
default) and where the newest version of one is published, the image tag compose resolves, and how to run
one prompt headless (headless_argv).

Pitfalls (each pinned where named):
- claude's prompt goes after `--`, so one like `-x` is no option (test_domain.Flavours.test_headless)
- grok ignores piped input next to a prompt: the prompt goes ahead of it, through --prompt-file (run.sh "a
  prompt and piped input: one stream on --prompt-file, the prompt first")
- grok's channels are stable and alpha, so `upgrade latest` is refused for grok (golden nexus/upgrade-args)
"""


class Flavour:
    name = ""
    cli = ""
    pin_var = ""
    tag_var = ""
    channels = ()
    newest = ""   # a URL with {channel} in it

    def newest_url(self, channel):
        return self.newest.replace("{channel}", channel)

    @property
    def image_tag(self):
        """The tag in the compose file: the fixed tag if set, else the pin, else latest (compose expands it)."""
        return "${%s:-${%s:-latest}}" % (self.tag_var, self.pin_var)

    def headless_argv(self, prompt, piped, flags):
        """The CLI's command line for one prompt, and a prompt to send ahead of the piped input (for a CLI
        that ignores piped input next to a prompt), or ""."""
        raise NotImplementedError


class Claude(Flavour):
    name = "claude"
    cli = "claude"
    pin_var = "CLAUDE_VERSION"
    tag_var = "CLAUDE_TAG"
    channels = ("latest", "stable")
    newest = "https://downloads.claude.ai/claude-code-releases/{channel}"

    def headless_argv(self, prompt, piped, flags):
        argv = ["claude", "-p"] + list(flags)
        if prompt:
            argv += ["--", prompt]   # after --, so a prompt can't be taken for an option
        return argv, ""


class Grok(Flavour):
    name = "grok"
    cli = "grok"
    pin_var = "GROK_VERSION"
    tag_var = "GROK_TAG"
    channels = ("stable", "alpha")
    newest = "https://x.ai/cli/{channel}"

    def headless_argv(self, prompt, piped, flags):
        if piped:
            return ["grok", "--prompt-file", "/dev/stdin"] + list(flags), prompt
        return ["grok", "--single=" + prompt] + list(flags), ""


FLAVOURS = {f.name: f for f in (Claude(), Grok())}


def get(name):
    """The Flavour called NAME, or None for one nothing here knows."""
    return FLAVOURS.get(name)

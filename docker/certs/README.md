# Extra trusted certificates (local only)

Any `*.crt` file placed here is added to the trust store of every Stratal image at build time. The files themselves are ignored by git, because they are specific to one machine.

**Why this exists.** On the owner's laptop, Norton Antivirus inspects HTTPS traffic. It re-signs every connection with its own root certificate, which Windows trusts but a Linux container does not. Without that root, `uv`, `pip` and model downloads fail inside containers with `invalid peer certificate: UnknownIssuer`.

**What is here on that machine.** The public Norton "Web/Mail Shield Root" certificate, exported from the Windows certificate store. It contains no secret. Trusting it inside the containers matches what the host already does; it does not turn any verification off.

**On a machine without TLS inspection** (for example CI), leave this folder empty. The build works unchanged.

**To stop needing it,** exclude the affected sites from HTTPS scanning in the antivirus, or turn that feature off. That is the owner's decision.

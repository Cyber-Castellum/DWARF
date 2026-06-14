from profile_manager.backends.base import BackendArtifacts
from profile_manager.profiles import compose_template


class LocalDevnetBackend:
    """Thin adapter over the existing local devnet emitter.

    Delegates to profiles.compose_template so local behavior is unchanged; this
    only exposes it behind the Backend seam for parity and testing. The live
    deploy path (profiles.deploy_command) is intentionally untouched.
    """
    name = "local"

    def render(self, profile, scenario=None) -> BackendArtifacts:
        return BackendArtifacts(
            backend=self.name,
            files={"docker-compose.yml": compose_template(profile)},
            summary={
                "compose_project": profile.compose_project,
                "node_count": profile.node_count,
                "amaru_node_count": profile.amaru_node_count,
            },
        )

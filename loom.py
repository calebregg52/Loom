import spotipy
import yaml
from pathlib import Path
from spotipy import SpotifyOAuth

DEFAULT_CREDS = {
    "SPOTIFY_CLIENT_ID": None,
    "SPOTIFY_CLIENT_SECRET": None,
}

class SpotifyClient:

    def __init__(self):

        self.secret_path = Path("secret.yaml")
        self.get_secret()
        self.generate_spotify_client()

    def get_secret(self) -> None:

        if not self.secret_path.exists():

            #need to bullet proof this eventually
            DEFAULT_CREDS["SPOTIFY_CLIENT_ID"] = str(input("Spotify client ID: ").strip())
            DEFAULT_CREDS["SPOTIFY_CLIENT_SECRET"] = str(input("Spotify client secret: ").strip())

            self.secret_path.parent.mkdir(parents=True, exist_ok=True)
            self.secret_path.write_text(
                yaml.dump(DEFAULT_CREDS, default_flow_style=False)
            )
            print(f"{self.secret_path} created successfully.")
            return DEFAULT_CREDS.copy()

        creds = yaml.safe_load(self.secret_path.read_text()) or {}
        return creds

    def generate_spotify_client(self) -> None:

        with self.secret_path.open("r") as f:
            creds = yaml.safe_load(f) or {}
        try:
            self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=creds["SPOTIFY_CLIENT_ID"],
                client_secret=creds["SPOTIFY_CLIENT_SECRET"],
                redirect_uri="http://localhost:8080/callback",
                scope="user-library-modify"
            ))
            print("Spotify client generated successfully.")
        except Exception as e:
            print(f"Error generating Spotify client: {e}")

if __name__ == "__main__":
    client = SpotifyClient()
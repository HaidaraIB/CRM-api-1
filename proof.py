"""
Interactive helper: compute Meta appsecret_proof = HMAC-SHA256(app_secret, access_token).
Use the same access token you pass as access_token= on the Graph API request.
"""
import hashlib
import hmac


def main() -> None:
    app_secret = input("App secret: ").strip()
    access_token = input("Access token: ").strip()

    if not app_secret or not access_token:
        print("Error: both app secret and access token are required.")
        return

    proof = hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    print("\nappsecret_proof:")
    print(proof)


if __name__ == "__main__":
    main()

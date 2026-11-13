from pydantic import BaseModel, EmailStr


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    # The Terms + Privacy checkbox. Defaults to False so a missing field gets the product's own
    # TERMS_NOT_ACCEPTED answer (422) rather than a generic "field required".
    accepted_terms: bool = False


class TokenRequest(BaseModel):
    token: str


class EmailRequest(BaseModel):
    email: EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MfaChallengeRequest(BaseModel):
    mfa_ticket: str
    code: str


class TotpVerifyRequest(BaseModel):
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    refresh_token: str | None = None


class MfaDisableRequest(BaseModel):
    password: str
    code: str


class MfaRegenerateRequest(BaseModel):
    password: str


class GoogleStartRequest(BaseModel):
    invite_token: str | None = None
    next: str | None = None
    # The Terms + Privacy checkbox, as on email sign-up. Only consulted if this sign-in would
    # create a NEW account; an existing account signs in without it.
    accepted_terms: bool = False


class GoogleExchangeRequest(BaseModel):
    ticket: str

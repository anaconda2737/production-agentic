from pydantic import BaseModel, Field, model_validator


class CaseResolveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)


class CitationSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=5, ge=1, le=100)


class PartyCooccurrenceRequest(BaseModel):
    query: str | None = Field(default=None, min_length=5, max_length=1000)
    party_a: str | None = Field(default=None, min_length=2, max_length=500)
    party_b: str | None = Field(default=None, min_length=2, max_length=500)
    limit: int = Field(default=50, ge=1, le=500)

    @model_validator(mode="after")
    def validate_party_input(self):
        if self.query is None and not (self.party_a and self.party_b):
            raise ValueError("Provide a 'query' with both parties or provide 'party_a' and 'party_b'.")
        if (self.party_a is None) != (self.party_b is None):
            raise ValueError("Provide both 'party_a' and 'party_b'.")
        return self


class ProvisionSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=50, ge=1, le=500)


class PassageSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=3000)
    limit: int = Field(default=8, ge=1, le=50)
    court: str | None = None
    year_from: int | None = Field(default=None, ge=1800, le=2200)
    year_to: int | None = Field(default=None, ge=1800, le=2200)
    provision_key: str | None = None


class AskRequest(BaseModel):
    query: str = Field(min_length=2, max_length=3000)
    limit: int = Field(default=8, ge=1, le=20)


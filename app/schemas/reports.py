from pydantic import BaseModel, Field


class ReportQuestionRequest(BaseModel):
    reason: str = Field(pattern="^(wrong_answer|unclear|offensive|other)$")
    comment: str | None = Field(default=None, max_length=500)


class ReportQuestionResponse(BaseModel):
    reported: bool = True

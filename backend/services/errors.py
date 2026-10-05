"""Expected errors that searches report without claiming no path exists."""


class WikipediaError(Exception):
    pass


class InvalidArticle(ValueError):
    pass


class SearchLimit(Exception):
    pass


class SearchBusy(Exception):
    pass


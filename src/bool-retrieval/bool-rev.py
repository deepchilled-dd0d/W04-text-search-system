"""Generate 50 English terms and 100 randomly aggregated documents."""

import json
import random
import re


TERMS = [
	"apple", "bridge", "candle", "dolphin", "eagle", "forest", "garden",
	"harbor", "island", "jacket", "kitten", "lantern", "mountain", "notebook",
	"ocean", "pencil", "quartz", "river", "sunset", "thunder", "umbrella",
	"village", "window", "xylophone", "yacht", "zephyr", "adventure", "balance",
	"curiosity", "discovery", "energy", "freedom", "gratitude", "harmony",
	"imagination", "journey", "knowledge", "laughter", "momentum", "nature",
	"opportunity", "patience", "question", "rainbow", "strength", "thought",
	"understanding", "vision", "wonder", "youth",
]


def build_documents(
	terms: list[str], count: int = 100, seed: int | None = None
) -> list[dict[str, object]]:
	"""Create documents containing randomly selected, aggregated terms."""
	if len(terms) != 50 or len(set(terms)) != 50:
		raise ValueError("terms must contain exactly 50 unique English terms")

	generator = random.Random(seed)
	documents = []
	for document_id in range(1, count + 1):
		size = generator.randint(3, 12)
		document_terms = generator.choices(terms, k=size)
		frequencies = {
			term: document_terms.count(term) for term in sorted(set(document_terms))
		}
		documents.append(
			{
				"id": document_id,
				"terms": document_terms,
				"term_frequencies": frequencies,
			}
		)
	return documents


def build_incidence_matrix(
	documents: list[dict[str, object]], terms: list[str]
) -> list[list[int]]:
	"""Build a document-term matrix using term frequencies as entries."""
	matrix = []
	for document in documents:
		frequencies = document["term_frequencies"]
		matrix.append([frequencies.get(term, 0) for term in terms])
	return matrix


def boolean_and(left: set[int], right: set[int]) -> set[int]:
	"""Return documents containing both operands."""
	return left & right


def boolean_or(left: set[int], right: set[int]) -> set[int]:
	"""Return documents containing either operand."""
	return left | right


def boolean_not(documents: set[int], universe: set[int]) -> set[int]:
	"""Return documents that do not contain the operand."""
	return universe - documents


def evaluate_query(
	query: str,
	documents: list[dict[str, object]],
	terms: list[str],
) -> list[int]:
	"""Evaluate a Boolean query containing terms, parentheses, AND, OR, and NOT."""
	universe = {int(document["id"]) for document in documents}
	postings = {
		term.upper(): {
			int(document["id"])
			for document in documents
			if document["term_frequencies"].get(term, 0) > 0
		}
		for term in terms
	}
	tokens = re.findall(r"\(|\)|\b(?:AND|OR|NOT)\b|[A-Za-z]+", query.upper())
	position = 0

	def parse_or() -> set[int]:
		nonlocal position
		result = parse_and()
		while position < len(tokens) and tokens[position] == "OR":
			position += 1
			result = boolean_or(result, parse_and())
		return result

	def parse_and() -> set[int]:
		nonlocal position
		result = parse_not()
		while position < len(tokens) and tokens[position] == "AND":
			position += 1
			result = boolean_and(result, parse_not())
		return result

	def parse_not() -> set[int]:
		nonlocal position
		if position < len(tokens) and tokens[position] == "NOT":
			position += 1
			return boolean_not(parse_not(), universe)
		return parse_primary()

	def parse_primary() -> set[int]:
		nonlocal position
		if position >= len(tokens):
			raise ValueError("Incomplete query")

		token = tokens[position]
		if token == "(":
			position += 1
			result = parse_or()
			if position >= len(tokens) or tokens[position] != ")":
				raise ValueError("Missing closing parenthesis")
			position += 1
			return result

		if token not in postings:
			raise ValueError(f"Unknown term: {token.lower()}")
		position += 1
		return postings[token]

	result = parse_or()
	if position != len(tokens):
		raise ValueError(f"Unexpected token: {tokens[position]}")
	return sorted(result)


def generate_queries(
	terms: list[str], count: int = 5, seed: int | None = None
) -> list[str]:
	"""Generate reproducible compound Boolean queries for testing."""
	generator = random.Random(seed)
	queries = []
	for _ in range(count):
		term_one, term_two, term_three = generator.sample(terms, 3)
		operator_one = generator.choice(["AND", "OR"])
		operator_two = generator.choice(["AND", "OR"])
		queries.append(
			f"({term_one} {operator_one} {term_two}) "
			f"{operator_two} NOT {term_three}"
		)
	return queries


if __name__ == "__main__":
	documents = build_documents(TERMS, seed=42)
	incidence_matrix = build_incidence_matrix(documents, TERMS)
	queries = generate_queries(TERMS, count=5, seed=42)
	queries.append("apple OR NOT eagle AND zephyr")
	query_results = {
		query: evaluate_query(query, documents, TERMS) for query in queries
	}
	print(
		json.dumps(
			{
				"boolean_queries": query_results,
			},
			indent=2,
		)
	)
    



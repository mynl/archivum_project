#![allow(non_local_definitions)]

use pyo3::prelude::*;
use pyo3::types::PyList;
use fuzzy_matcher::skim::SkimMatcherV2;
use fuzzy_matcher::FuzzyMatcher as FuzzyMatcherTrait;

/// A fuzzy search object initialized with a list of strings.
#[pyclass]
struct FuzzyMatcher {
    items: Vec<String>,
    matcher: SkimMatcherV2,
}

#[pymethods]
impl FuzzyMatcher {
    #[new]
    fn new(strings: &PyList) -> PyResult<Self> {
        let items = strings.iter()
            .map(|s| s.extract::<String>())
            .collect::<Result<Vec<_>, _>>()?;
        Ok(FuzzyMatcher {
            items,
            matcher: SkimMatcherV2::default(),
        })
    }

    fn query(&self, query: &str, top_k: usize) -> (Vec<usize>, Vec<i64>) {
        let mut results: Vec<_> = self.items.iter().enumerate()
            .filter_map(|(i, s)| self.matcher.fuzzy_match(s, query).map(|score| (i, score)))
            .collect();

        results.sort_by(|a, b| b.1.cmp(&a.1)); // descending
        results.truncate(top_k);
        // results
        let (indices, scores): (Vec<_>, Vec<_>) = results.into_iter().unzip();
        (indices, scores)
    }
}


#[pyclass]
struct FuzzyMatcherMulti {
    items: Vec<String>,
    matcher: SkimMatcherV2,
}

#[pymethods]
impl FuzzyMatcherMulti {
    #[new]
    fn new(strings: &PyList) -> PyResult<Self> {
        let items = strings.iter()
            .map(|s| s.extract::<String>())
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            items,
            matcher: SkimMatcherV2::default(),
        })
    }

    fn query(&self, query: &str, top_k: usize) -> (Vec<usize>, Vec<i64>) {
        let tokens: Vec<_> = query.split_whitespace().collect();

        let mut results = Vec::with_capacity(top_k * 2);

        for (i, candidate) in self.items.iter().enumerate() {
            let mut token_scores = Vec::with_capacity(tokens.len());

            for &token in &tokens {
                match self.matcher.fuzzy_match(candidate, token) {
                    Some(score) => token_scores.push(score),
                    None => {
                        token_scores.clear();
                        break;
                    }
                }
            }

            if !token_scores.is_empty() {
                let total_score: i64 = token_scores.iter().sum();
                results.push((i, total_score));
            }
        }

        results.sort_by(|a, b| b.1.cmp(&a.1));
        results.truncate(top_k);
        // results
        let (indices, scores): (Vec<_>, Vec<_>) = results.into_iter().unzip();
        (indices, scores)
    }

    fn query_compact(&self, query: &str, top_k: usize, min_score: i64) -> (Vec<usize>, Vec<i64>) {
        let tokens: Vec<&str> = query.split_whitespace().collect();
        let mut results = Vec::new();

        for (i, candidate) in self.items.iter().enumerate() {
            let mut token_scores = Vec::new();
            let mut matched_indices = Vec::new();

            for &token in &tokens {
                match self.matcher.fuzzy_indices(candidate, token) {
                    Some((score, idxs)) if score >= min_score => {
                        token_scores.push(score);
                        matched_indices.extend(idxs);
                    }
                    _ => {
                        token_scores.clear();
                        break;
                    }
                }
            }

            if !token_scores.is_empty() && !matched_indices.is_empty() {
                matched_indices.sort_unstable();
                let span = matched_indices.last().unwrap() - matched_indices.first().unwrap() + 1;
                let coverage = matched_indices.len();

                let compactness = coverage as f64 / span as f64;
                let score: i64 = (token_scores.iter().map(|s| *s as f64).sum::<f64>() * compactness) as i64;

                results.push((i, score));
            }
        }

        results.sort_by(|a, b| b.1.cmp(&a.1));
        let (indices, scores): (Vec<usize>, Vec<i64>) = results.into_iter().take(top_k).unzip();
        (indices, scores)
    }

}

#[pymodule]
fn rustfuzz(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<FuzzyMatcher>()?;
    m.add_class::<FuzzyMatcherMulti>()?;
    Ok(())
}

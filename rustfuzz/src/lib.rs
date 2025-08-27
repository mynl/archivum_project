#![allow(non_local_definitions)]

use pyo3::prelude::*;
use pyo3::types::PyList;
use fuzzy_matcher::skim::SkimMatcherV2;
use fuzzy_matcher::FuzzyMatcher as FuzzyMatcherTrait;
// use std::collections::HashSet;


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

#[pyclass] 
struct FieldAwareFuzzy {
    data: Vec<(String, String, String, String)>, // (author, title, journal, year)
    matcher: SkimMatcherV2,
}

#[pymethods]
impl FieldAwareFuzzy {
    #[new]
    fn new(pylist: &PyList) -> PyResult<Self> {
        let data = pylist.iter()
            .map(|item| {
                let tuple: (String, String, String, String) = item.extract()?;
                Ok(tuple)
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(Self { data, matcher: SkimMatcherV2::default() })
    }

    fn query_html(&self, query: &str, top_k: usize) -> Vec<String> {
        let tokens: Vec<&str> = query.split_whitespace().collect();
        let mut results = Vec::new();

        for (author, title, journal, year) in &self.data {
            let mut token_scores = Vec::new();
            let mut field_highlights: [Vec<usize>; 3] = [Vec::new(), Vec::new(), Vec::new()];

            for &token in &tokens {
                let mut best_score: Option<(i64, usize, Vec<usize>)> = None;

                for (fid, field_val) in [author, title, journal].iter().enumerate() {
                    if let Some((score, idxs)) = self.matcher.fuzzy_indices(field_val, token) {
                        if best_score.is_none() || score > best_score.as_ref().unwrap().0 {
                            best_score = Some((score, fid, idxs));
                        }
                    }
                }

                if let Some((score, fid, idxs)) = best_score {
                    token_scores.push(score);
                    field_highlights[fid].extend(idxs);
                } else {
                    token_scores.clear();
                    break;
                }
            }

            if !token_scores.is_empty() {
                let score: i64 = token_scores.iter().sum();

                let highlight = |s: &str, idxs: &[usize]| -> String {
                    let mut result = String::new();
                    for (i, c) in s.chars().enumerate() {
                        if idxs.contains(&i) {
                            result.push_str(&format!("<mark>{}</mark>", c));
                        } else {
                            result.push(c);
                        }
                    }
                    result
                };

                let author_html  = highlight(author, &field_highlights[0]);
                let title_html   = highlight(title,  &field_highlights[1]);
                let journal_html = highlight(journal,&field_highlights[2]);

                let row = format!(
                    "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
                    author_html, title_html, journal_html, year, score
                );
                results.push((score, row));
            }
        }


        results.sort_by(|a, b| b.0.cmp(&a.0));
        results.truncate(top_k);
        results.into_iter().map(|(_, row)| row).collect()
    }
}

#[pyclass]
struct FieldAwareFuzzy2 {
    data: Vec<(String, String, String, String)>, // (author, title, journal, year)
    matcher: SkimMatcherV2,
}

#[pymethods]
impl FieldAwareFuzzy2 {
    #[new]
    fn new(pylist: &PyList) -> PyResult<Self> {
        let data = pylist.iter()
            .map(|item| {
                let tuple: (String, String, String, String) = item.extract()?;
                Ok(tuple)
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(Self { data, matcher: SkimMatcherV2::default() })
    }

    fn query_html(&self, query: &str, top_k: usize, url: &str) -> PyResult<(Vec<String>, Vec<usize>)> {
        let tokens: Vec<&str> = query.split_whitespace().collect();
        let mut scored: Vec<(i64, usize, String)> = Vec::new();

        for (i, (author, title, journal, year)) in self.data.iter().enumerate() {
            let concat = format!("{} {} {}", author, title, journal);

            let mut token_scores = Vec::new();
            let mut field_highlights: [Vec<usize>; 3] = [Vec::new(), Vec::new(), Vec::new()];

            for &token in &tokens {
                if let Some((score, _)) = self.matcher.fuzzy_indices(&concat, token) {
                    token_scores.push(score);
                    for (fid, field_val) in [author, title, journal].iter().enumerate() {
                        if let Some((_, idxs)) = self.matcher.fuzzy_indices(field_val, token) {
                            field_highlights[fid].extend(idxs);
                            break;
                        }
                    }
                } else {
                    token_scores.clear();
                    break;
                }
            }

            if !token_scores.is_empty() {
                let score: i64 = token_scores.iter().sum();

                let highlight = |s: &str, idxs: &[usize]| -> String {
                    let mut result = String::new();
                    for (i, c) in s.chars().enumerate() {
                        if idxs.contains(&i) {
                            result.push_str(&format!("<mark>{}</mark>", c));
                        } else {
                            result.push(c);
                        }
                    }
                    result
                };

                let row = format!(
                    "<tr><td><a href=\"{}?i={}\">{}</a></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
                    url,
                    i,
                    i,
                    highlight(author, &field_highlights[0]),
                    highlight(title,  &field_highlights[1]),
                    highlight(journal,&field_highlights[2]),
                    year,
                    score
                );

                scored.push((score, i, row));
            }
        }

        scored.sort_by(|a, b| b.0.cmp(&a.0));
        scored.truncate(top_k);

        let htmls = scored.iter().map(|(_, _, row)| row.clone()).collect();
        let indices = scored.iter().map(|(_, i, _)| *i).collect();

        Ok((htmls, indices))
    }

}



#[pymodule]
fn rustfuzz(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<FuzzyMatcher>()?;
    m.add_class::<FuzzyMatcherMulti>()?;
    m.add_class::<FieldAwareFuzzy>()?;
    m.add_class::<FieldAwareFuzzy2>()?;
    Ok(())
}

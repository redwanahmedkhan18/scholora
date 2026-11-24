# Citation diff report

_Generated 2026-10-04 06:07 UTC by `backend/scripts/verify_citations.py`._

This report does **not** certify any figure as correct. Only a human reading the
page can do that. What it does is shrink the job: instead of re-reading every
cited page, a reviewer works the **NOT_FOUND** table below, where the page no
longer appears to contain the value we stored.

| Verdict | Count | Meaning |
|---|---:|---|
| CONFIRMED | 23 | value found in the page text |
| NOT_FOUND | 55 | page read fine, value absent - **review these** |
| UNCHECKABLE | 20 | page blocked or unreachable from here |
| CITATION_TOO_COARSE | 1123 | cited page is a site homepage, not the page stating the figure |
| NOT_APPLICABLE | 2257 | internal bookkeeping or prose; not a claim a page would state |

**23 of 78 machine-checkable facts (29.5%) were found verbatim on their cited page.**

That percentage is the less important number. The one to act on is **1123**: world-claims - tuition, IELTS scores, deadlines - whose citation points at a site homepage. Those are not re-checkable by anyone, and because a homepage never returns 404 they score a clean bill of health from any link checker. A green link report on this dataset was measuring reachability and being read as verifiability.

## NOT_FOUND - needs a human

Most entries here are not errors. The usual causes, in rough order of frequency:
the figure sits behind a tab or in a PDF the fetcher cannot read; the page shows a
range where we stored a point value; or the university genuinely changed it. The
third case is the reason this report exists.

| Entity | id | Field | Stored value | Cited page |
|---|---:|---|---|---|
| program | 155 | `currency` | `KRW` | <https://postech.ac.kr/eng/> |
| program | 67 | `currency` | `JPY` | <https://www.waseda.jp/top/en> |
| program | 118 | `duration_months` | `24` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program | 67 | `duration_months` | `48` | <https://www.waseda.jp/top/en> |
| program | 155 | `tuition_annual` | `0` | <https://postech.ac.kr/eng/> |
| program | 118 | `tuition_annual` | `750` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program | 67 | `tuition_annual` | `1500000` | <https://www.waseda.jp/top/en> |
| program_intake | 254 | `month` | `3` | <https://postech.ac.kr/eng/> |
| program_intake | 138 | `month` | `4` | <https://www.waseda.jp/top/en> |
| program_intake | 139 | `month` | `9` | <https://www.waseda.jp/top/en> |
| program_intake | 254 | `term` | `MARCH` | <https://postech.ac.kr/eng/> |
| program_intake | 138 | `term` | `APRIL` | <https://www.waseda.jp/top/en> |
| program_intake | 254 | `year` | `2027` | <https://postech.ac.kr/eng/> |
| program_intake | 199 | `year` | `2027` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program_intake | 138 | `year` | `2027` | <https://www.waseda.jp/top/en> |
| program_requirement | 155 | `cgpa_scale` | `4.0` | <https://postech.ac.kr/eng/> |
| program_requirement | 118 | `cgpa_scale` | `4.0` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program_requirement | 67 | `cgpa_scale` | `4.0` | <https://www.waseda.jp/top/en> |
| program_requirement | 155 | `ielts_overall` | `6.5` | <https://postech.ac.kr/eng/> |
| program_requirement | 118 | `ielts_overall` | `6.5` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program_requirement | 1 | `ielts_overall` | `6.5` | <https://www.uta.edu/admissions> |
| program_requirement | 67 | `ielts_overall` | `6.5` | <https://www.waseda.jp/top/en> |
| program_requirement | 155 | `min_cgpa` | `3.3` | <https://postech.ac.kr/eng/> |
| program_requirement | 118 | `min_cgpa` | `3.0` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program_requirement | 67 | `min_cgpa` | `3.0` | <https://www.waseda.jp/top/en> |
| program_requirement | 155 | `min_qualification` | `Recognised bachelor's degree` | <https://postech.ac.kr/eng/> |
| program_requirement | 118 | `min_qualification` | `Recognised bachelor's degree` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program_requirement | 67 | `min_qualification` | `Higher Secondary Certificate or equivalent` | <https://www.waseda.jp/top/en> |
| program_requirement | 155 | `toefl_ibt` | `85` | <https://postech.ac.kr/eng/> |
| program_requirement | 118 | `toefl_ibt` | `90` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| program_requirement | 67 | `toefl_ibt` | `90` | <https://www.waseda.jp/top/en> |
| scholarship | 16 | `amount_value` | `0` | <https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters> |
| scholarship | 28 | `amount_value` | `1150` | <https://oead.at/en/to-austria/grants-and-scholarships> |
| scholarship | 22 | `amount_value` | `0` | <https://www.aalto.fi/en/international-students/scholarships-study-right-started-on-or-after-1-august-2025> |
| scholarship | 18 | `amount_value` | `0` | <https://www.aecid.es/en/scholarships-lectureships> |
| scholarship | 23 | `amount_value` | `10000` | <https://www.helsinki.fi/en/admissions-and-education/apply-bachelors-and-masters-programmes> |
| scholarship | 26 | `amount_value` | `0` | <https://www.kaust.edu.sa/en/study/admissions> |
| scholarship | 24 | `amount_value` | `0` | <https://www.moe.gov.sg/financial-matters/awards-scholarships> |
| scholarship | 20 | `amount_value` | `0` | <https://www.studyinjapan.go.jp/en/planning/scholarship/> |
| scholarship | 16 | `country` | `spain` | <https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters> |
| scholarship | 18 | `country` | `spain` | <https://www.aecid.es/en/scholarships-lectureships> |
| scholarship | 19 | `country` | `spain` | <https://www.fundacioncarolina.es/formacion/formacion-y-becas-de-la-fundacion-carolina/> |
| scholarship | 23 | `country` | `finland` | <https://www.helsinki.fi/en/admissions-and-education/apply-bachelors-and-masters-programmes> |
| scholarship | 26 | `country` | `saudi-arabia` | <https://www.kaust.edu.sa/en/study/admissions> |
| scholarship | 24 | `country` | `singapore` | <https://www.moe.gov.sg/financial-matters/awards-scholarships> |
| scholarship | 19 | `currency` | `EUR` | <https://www.fundacioncarolina.es/formacion/formacion-y-becas-de-la-fundacion-carolina/> |
| scholarship | 26 | `currency` | `USD` | <https://www.kaust.edu.sa/en/study/admissions> |
| scholarship | 24 | `currency` | `SGD` | <https://www.moe.gov.sg/financial-matters/awards-scholarships> |
| scholarship | 20 | `currency` | `JPY` | <https://www.studyinjapan.go.jp/en/planning/scholarship/> |
| university | 86 | `country` | `south-korea` | <https://postech.ac.kr/eng/> |
| university | 63 | `country` | `germany` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| university | 63 | `established_year` | `1870` | <https://www.rwth-aachen.de/go/id/a/?lidx=1> |
| university | 62 | `established_year` | `1868` | <https://www.tum.de/en/studies/fees/tuition> |
| university | 52 | `established_year` | `1882` | <https://www.waseda.jp/top/en> |
| university | 1 | `tuition_annual` | `28000` | <https://www.uta.edu/tuition> |

## CITATION_TOO_COARSE - cited page cannot show the figure

A specific number cited to a university homepage cannot be re-checked by
anyone, human or machine: the homepage does not state tuition, IELTS or
intake months. These will also pass a link checker forever, because the URL
resolves perfectly - which is exactly why they need listing separately.

The fix is to deepen the citation to the fees/admissions page that states the
figure, not to delete the data.

| Entity | Field | Cited page |
|---|---|---|
| country | `currency` | <https://studyinaustria.at/> |
| country | `currency` | <https://studyinsaudi.moe.gov.sa/> |
| country | `currency` | <https://www.moe.gov.sg/> |
| country | `currency` | <https://www.studyinturkiye.gov.tr/> |
| country | `currency` | <https://www.universidades.gob.es/> |
| country | `living_cost_max` | <https://studyinaustria.at/> |
| country | `living_cost_max` | <https://studyinsaudi.moe.gov.sa/> |
| country | `living_cost_max` | <https://www.moe.gov.sg/> |
| country | `living_cost_max` | <https://www.studyinturkiye.gov.tr/> |
| country | `living_cost_max` | <https://www.universidades.gob.es/> |
| country | `living_cost_min` | <https://studyinaustria.at/> |
| country | `living_cost_min` | <https://studyinsaudi.moe.gov.sa/> |
| country | `living_cost_min` | <https://www.moe.gov.sg/> |
| country | `living_cost_min` | <https://www.studyinturkiye.gov.tr/> |
| country | `living_cost_min` | <https://www.universidades.gob.es/> |
| country | `tuition_max` | <https://studyinaustria.at/> |
| country | `tuition_max` | <https://studyinsaudi.moe.gov.sa/> |
| country | `tuition_max` | <https://www.moe.gov.sg/> |
| country | `tuition_max` | <https://www.studyinturkiye.gov.tr/> |
| country | `tuition_max` | <https://www.universidades.gob.es/> |
| country | `tuition_min` | <https://studyinaustria.at/> |
| country | `tuition_min` | <https://studyinsaudi.moe.gov.sa/> |
| country | `tuition_min` | <https://www.moe.gov.sg/> |
| country | `tuition_min` | <https://www.studyinturkiye.gov.tr/> |
| country | `tuition_min` | <https://www.universidades.gob.es/> |
| program | `currency` | <https://en.nagoya-u.ac.jp/> |
| program | `currency` | <https://en.snu.ac.kr/> |
| program | `currency` | <https://tu-dresden.de/> |
| program | `currency` | <https://uwaterloo.ca/> |
| program | `currency` | <https://www.aalto.fi/en> |
| program | `currency` | <https://www.bogazici.edu.tr> |
| program | `currency` | <https://www.dcu.ie/> |
| program | `currency` | <https://www.global.hokudai.ac.jp/> |
| program | `currency` | <https://www.helsinki.fi/en> |
| program | `currency` | <https://www.hu-berlin.de/en> |
| program | `currency` | <https://www.itu.edu.tr> |
| program | `currency` | <https://www.jku.at> |
| program | `currency` | <https://www.jyu.fi/en> |
| program | `currency` | <https://www.kaist.ac.kr/en/> |
| program | `currency` | <https://www.kau.edu.sa> |
| program | `currency` | <https://www.kaust.edu.sa> |
| program | `currency` | <https://www.keio.ac.jp/en/> |
| program | `currency` | <https://www.kfupm.edu.sa> |
| program | `currency` | <https://www.kit.edu/english/> |
| program | `currency` | <https://www.korea.edu/> |
| program | `currency` | <https://www.ksu.edu.sa> |
| program | `currency` | <https://www.ku.edu.tr> |
| program | `currency` | <https://www.kyoto-u.ac.jp/en> |
| program | `currency` | <https://www.kyushu-u.ac.jp/en/> |
| program | `currency` | <https://www.lmu.de/en/> |
| program | `currency` | <https://www.lut.fi/en> |
| program | `currency` | <https://www.mcgill.ca/> |
| program | `currency` | <https://www.metu.edu.tr> |
| program | `currency` | <https://www.monash.edu.my/> |
| program | `currency` | <https://www.monash.edu/> |
| program | `currency` | <https://www.ntu.edu.sg> |
| program | `currency` | <https://www.nus.edu.sg> |
| program | `currency` | <https://www.osaka-u.ac.jp/en> |
| program | `currency` | <https://www.oulu.fi/en> |
| program | `currency` | <https://www.smu.edu.sg> |
| program | `currency` | <https://www.sydney.edu.au/> |
| program | `currency` | <https://www.tcd.ie/> |
| program | `currency` | <https://www.tohoku.ac.jp/en/> |
| program | `currency` | <https://www.tsukuba.ac.jp/en/> |
| program | `currency` | <https://www.tu.berlin/en/> |
| program | `currency` | <https://www.tugraz.at> |
| program | `currency` | <https://www.tum.de/en/> |
| program | `currency` | <https://www.tuni.fi/en> |
| program | `currency` | <https://www.tuwien.at> |
| program | `currency` | <https://www.u-tokyo.ac.jp/en/> |
| program | `currency` | <https://www.ualberta.ca/> |
| program | `currency` | <https://www.ub.edu> |
| program | `currency` | <https://www.ubc.ca/> |
| program | `currency` | <https://www.uc3m.es> |
| program | `currency` | <https://www.ucc.ie/> |
| program | `currency` | <https://www.ucd.ie/> |
| program | `currency` | <https://www.ucm.es> |
| program | `currency` | <https://www.uef.fi/en> |
| program | `currency` | <https://www.uibk.ac.at> |
| program | `currency` | <https://www.um.edu.my/> |
| program | `currency` | <https://www.uni-freiburg.de/en> |
| program | `currency` | <https://www.uni-heidelberg.de/en> |
| program | `currency` | <https://www.uni-stuttgart.de/en/> |
| program | `currency` | <https://www.unimelb.edu.au/> |
| program | `currency` | <https://www.universityofgalway.ie/> |
| program | `currency` | <https://www.univie.ac.at> |
| program | `currency` | <https://www.unsw.edu.au/> |
| program | `currency` | <https://www.upf.edu> |
| program | `currency` | <https://www.upm.edu.my/> |
| program | `currency` | <https://www.uq.edu.au/> |
| program | `currency` | <https://www.utm.my/> |
| program | `currency` | <https://www.utoronto.ca/> |
| program | `currency` | <https://www.utu.fi/en> |
| program | `duration_months` | <https://en.nagoya-u.ac.jp/> |
| program | `duration_months` | <https://en.snu.ac.kr/> |
| program | `duration_months` | <https://tu-dresden.de/> |
| program | `duration_months` | <https://uwaterloo.ca/> |
| program | `duration_months` | <https://www.aalto.fi/en> |
| program | `duration_months` | <https://www.bogazici.edu.tr> |
| program | `duration_months` | <https://www.dcu.ie/> |
| program | `duration_months` | <https://www.global.hokudai.ac.jp/> |
| program | `duration_months` | <https://www.helsinki.fi/en> |
| program | `duration_months` | <https://www.hu-berlin.de/en> |
| program | `duration_months` | <https://www.itu.edu.tr> |
| program | `duration_months` | <https://www.jku.at> |
| program | `duration_months` | <https://www.jyu.fi/en> |
| program | `duration_months` | <https://www.kaist.ac.kr/en/> |
| program | `duration_months` | <https://www.kau.edu.sa> |
| program | `duration_months` | <https://www.kaust.edu.sa> |
| program | `duration_months` | <https://www.keio.ac.jp/en/> |
| program | `duration_months` | <https://www.kfupm.edu.sa> |
| program | `duration_months` | <https://www.kit.edu/english/> |
| program | `duration_months` | <https://www.korea.edu/> |
| program | `duration_months` | <https://www.ksu.edu.sa> |
| program | `duration_months` | <https://www.ku.edu.tr> |
| program | `duration_months` | <https://www.kyoto-u.ac.jp/en> |
| program | `duration_months` | <https://www.kyushu-u.ac.jp/en/> |
| program | `duration_months` | <https://www.lmu.de/en/> |
| program | `duration_months` | <https://www.lut.fi/en> |
| program | `duration_months` | <https://www.mcgill.ca/> |
| program | `duration_months` | <https://www.metu.edu.tr> |
| program | `duration_months` | <https://www.monash.edu.my/> |
| program | `duration_months` | <https://www.monash.edu/> |
| program | `duration_months` | <https://www.ntu.edu.sg> |
| program | `duration_months` | <https://www.nus.edu.sg> |
| program | `duration_months` | <https://www.osaka-u.ac.jp/en> |
| program | `duration_months` | <https://www.oulu.fi/en> |
| program | `duration_months` | <https://www.smu.edu.sg> |
| program | `duration_months` | <https://www.sydney.edu.au/> |
| program | `duration_months` | <https://www.tcd.ie/> |
| program | `duration_months` | <https://www.tohoku.ac.jp/en/> |
| program | `duration_months` | <https://www.tsukuba.ac.jp/en/> |
| program | `duration_months` | <https://www.tu.berlin/en/> |
| program | `duration_months` | <https://www.tugraz.at> |
| program | `duration_months` | <https://www.tum.de/en/> |
| program | `duration_months` | <https://www.tuni.fi/en> |
| program | `duration_months` | <https://www.tuwien.at> |
| program | `duration_months` | <https://www.u-tokyo.ac.jp/en/> |
| program | `duration_months` | <https://www.ualberta.ca/> |
| program | `duration_months` | <https://www.ub.edu> |
| program | `duration_months` | <https://www.ubc.ca/> |
| program | `duration_months` | <https://www.uc3m.es> |
| program | `duration_months` | <https://www.ucc.ie/> |
| program | `duration_months` | <https://www.ucd.ie/> |
| program | `duration_months` | <https://www.ucm.es> |
| program | `duration_months` | <https://www.uef.fi/en> |
| program | `duration_months` | <https://www.uibk.ac.at> |
| program | `duration_months` | <https://www.um.edu.my/> |
| program | `duration_months` | <https://www.uni-freiburg.de/en> |
| program | `duration_months` | <https://www.uni-heidelberg.de/en> |
| program | `duration_months` | <https://www.uni-stuttgart.de/en/> |
| program | `duration_months` | <https://www.unimelb.edu.au/> |
| program | `duration_months` | <https://www.universityofgalway.ie/> |
| program | `duration_months` | <https://www.univie.ac.at> |
| program | `duration_months` | <https://www.unsw.edu.au/> |
| program | `duration_months` | <https://www.upf.edu> |
| program | `duration_months` | <https://www.upm.edu.my/> |
| program | `duration_months` | <https://www.uq.edu.au/> |
| program | `duration_months` | <https://www.utm.my/> |
| program | `duration_months` | <https://www.utoronto.ca/> |
| program | `duration_months` | <https://www.utu.fi/en> |
| program | `tuition_annual` | <https://en.nagoya-u.ac.jp/> |
| program | `tuition_annual` | <https://en.snu.ac.kr/> |
| program | `tuition_annual` | <https://tu-dresden.de/> |
| program | `tuition_annual` | <https://uwaterloo.ca/> |
| program | `tuition_annual` | <https://www.aalto.fi/en> |
| program | `tuition_annual` | <https://www.bogazici.edu.tr> |
| program | `tuition_annual` | <https://www.dcu.ie/> |
| program | `tuition_annual` | <https://www.global.hokudai.ac.jp/> |
| program | `tuition_annual` | <https://www.helsinki.fi/en> |
| program | `tuition_annual` | <https://www.hu-berlin.de/en> |
| program | `tuition_annual` | <https://www.itu.edu.tr> |
| program | `tuition_annual` | <https://www.jku.at> |
| program | `tuition_annual` | <https://www.jyu.fi/en> |
| program | `tuition_annual` | <https://www.kaist.ac.kr/en/> |
| program | `tuition_annual` | <https://www.kau.edu.sa> |
| program | `tuition_annual` | <https://www.kaust.edu.sa> |
| program | `tuition_annual` | <https://www.keio.ac.jp/en/> |
| program | `tuition_annual` | <https://www.kfupm.edu.sa> |
| program | `tuition_annual` | <https://www.kit.edu/english/> |
| program | `tuition_annual` | <https://www.korea.edu/> |
| program | `tuition_annual` | <https://www.ksu.edu.sa> |
| program | `tuition_annual` | <https://www.ku.edu.tr> |
| program | `tuition_annual` | <https://www.kyoto-u.ac.jp/en> |
| program | `tuition_annual` | <https://www.kyushu-u.ac.jp/en/> |
| program | `tuition_annual` | <https://www.lmu.de/en/> |
| program | `tuition_annual` | <https://www.lut.fi/en> |
| program | `tuition_annual` | <https://www.mcgill.ca/> |
| program | `tuition_annual` | <https://www.metu.edu.tr> |
| program | `tuition_annual` | <https://www.monash.edu.my/> |
| program | `tuition_annual` | <https://www.monash.edu/> |
| program | `tuition_annual` | <https://www.ntu.edu.sg> |
| program | `tuition_annual` | <https://www.nus.edu.sg> |
| program | `tuition_annual` | <https://www.osaka-u.ac.jp/en> |
| program | `tuition_annual` | <https://www.oulu.fi/en> |
| program | `tuition_annual` | <https://www.smu.edu.sg> |
| program | `tuition_annual` | <https://www.sydney.edu.au/> |
| program | `tuition_annual` | <https://www.tcd.ie/> |
| program | `tuition_annual` | <https://www.tohoku.ac.jp/en/> |
| program | `tuition_annual` | <https://www.tsukuba.ac.jp/en/> |
| program | `tuition_annual` | <https://www.tu.berlin/en/> |
| program | `tuition_annual` | <https://www.tugraz.at> |
| program | `tuition_annual` | <https://www.tum.de/en/> |
| program | `tuition_annual` | <https://www.tuni.fi/en> |
| program | `tuition_annual` | <https://www.tuwien.at> |
| program | `tuition_annual` | <https://www.u-tokyo.ac.jp/en/> |
| program | `tuition_annual` | <https://www.ualberta.ca/> |
| program | `tuition_annual` | <https://www.ub.edu> |
| program | `tuition_annual` | <https://www.ubc.ca/> |
| program | `tuition_annual` | <https://www.uc3m.es> |
| program | `tuition_annual` | <https://www.ucc.ie/> |
| program | `tuition_annual` | <https://www.ucd.ie/> |
| program | `tuition_annual` | <https://www.ucm.es> |
| program | `tuition_annual` | <https://www.uef.fi/en> |
| program | `tuition_annual` | <https://www.uibk.ac.at> |
| program | `tuition_annual` | <https://www.um.edu.my/> |
| program | `tuition_annual` | <https://www.uni-freiburg.de/en> |
| program | `tuition_annual` | <https://www.uni-heidelberg.de/en> |
| program | `tuition_annual` | <https://www.uni-stuttgart.de/en/> |
| program | `tuition_annual` | <https://www.unimelb.edu.au/> |
| program | `tuition_annual` | <https://www.universityofgalway.ie/> |
| program | `tuition_annual` | <https://www.univie.ac.at> |
| program | `tuition_annual` | <https://www.unsw.edu.au/> |
| program | `tuition_annual` | <https://www.upf.edu> |
| program | `tuition_annual` | <https://www.upm.edu.my/> |
| program | `tuition_annual` | <https://www.uq.edu.au/> |
| program | `tuition_annual` | <https://www.utm.my/> |
| program | `tuition_annual` | <https://www.utoronto.ca/> |
| program | `tuition_annual` | <https://www.utu.fi/en> |
| program_intake | `month` | <https://en.nagoya-u.ac.jp/> |
| program_intake | `month` | <https://en.snu.ac.kr/> |
| program_intake | `month` | <https://tu-dresden.de/> |
| program_intake | `month` | <https://uwaterloo.ca/> |
| program_intake | `month` | <https://www.aalto.fi/en> |
| program_intake | `month` | <https://www.bogazici.edu.tr> |
| program_intake | `month` | <https://www.dcu.ie/> |
| program_intake | `month` | <https://www.global.hokudai.ac.jp/> |
| program_intake | `month` | <https://www.helsinki.fi/en> |
| program_intake | `month` | <https://www.hu-berlin.de/en> |
| program_intake | `month` | <https://www.itu.edu.tr> |
| program_intake | `month` | <https://www.jku.at> |
| program_intake | `month` | <https://www.jyu.fi/en> |
| program_intake | `month` | <https://www.kaist.ac.kr/en/> |
| program_intake | `month` | <https://www.kau.edu.sa> |
| program_intake | `month` | <https://www.kaust.edu.sa> |
| program_intake | `month` | <https://www.keio.ac.jp/en/> |
| program_intake | `month` | <https://www.kfupm.edu.sa> |
| program_intake | `month` | <https://www.kit.edu/english/> |
| program_intake | `month` | <https://www.korea.edu/> |
| program_intake | `month` | <https://www.ksu.edu.sa> |
| program_intake | `month` | <https://www.ku.edu.tr> |
| program_intake | `month` | <https://www.kyoto-u.ac.jp/en> |
| program_intake | `month` | <https://www.kyushu-u.ac.jp/en/> |
| program_intake | `month` | <https://www.lmu.de/en/> |
| program_intake | `month` | <https://www.lut.fi/en> |
| program_intake | `month` | <https://www.mcgill.ca/> |
| program_intake | `month` | <https://www.metu.edu.tr> |
| program_intake | `month` | <https://www.monash.edu.my/> |
| program_intake | `month` | <https://www.monash.edu/> |
| program_intake | `month` | <https://www.ntu.edu.sg> |
| program_intake | `month` | <https://www.nus.edu.sg> |
| program_intake | `month` | <https://www.osaka-u.ac.jp/en> |
| program_intake | `month` | <https://www.oulu.fi/en> |
| program_intake | `month` | <https://www.smu.edu.sg> |
| program_intake | `month` | <https://www.sydney.edu.au/> |
| program_intake | `month` | <https://www.tcd.ie/> |
| program_intake | `month` | <https://www.tohoku.ac.jp/en/> |
| program_intake | `month` | <https://www.tsukuba.ac.jp/en/> |
| program_intake | `month` | <https://www.tu.berlin/en/> |
| program_intake | `month` | <https://www.tugraz.at> |
| program_intake | `month` | <https://www.tum.de/en/> |
| program_intake | `month` | <https://www.tuni.fi/en> |
| program_intake | `month` | <https://www.tuwien.at> |
| program_intake | `month` | <https://www.u-tokyo.ac.jp/en/> |
| program_intake | `month` | <https://www.ualberta.ca/> |
| program_intake | `month` | <https://www.ub.edu> |
| program_intake | `month` | <https://www.ubc.ca/> |
| program_intake | `month` | <https://www.uc3m.es> |
| program_intake | `month` | <https://www.ucc.ie/> |
| program_intake | `month` | <https://www.ucd.ie/> |
| program_intake | `month` | <https://www.ucm.es> |
| program_intake | `month` | <https://www.uef.fi/en> |
| program_intake | `month` | <https://www.uibk.ac.at> |
| program_intake | `month` | <https://www.um.edu.my/> |
| program_intake | `month` | <https://www.uni-freiburg.de/en> |
| program_intake | `month` | <https://www.uni-heidelberg.de/en> |
| program_intake | `month` | <https://www.uni-stuttgart.de/en/> |
| program_intake | `month` | <https://www.unimelb.edu.au/> |
| program_intake | `month` | <https://www.universityofgalway.ie/> |
| program_intake | `month` | <https://www.univie.ac.at> |
| program_intake | `month` | <https://www.unsw.edu.au/> |
| program_intake | `month` | <https://www.upf.edu> |
| program_intake | `month` | <https://www.upm.edu.my/> |
| program_intake | `month` | <https://www.uq.edu.au/> |
| program_intake | `month` | <https://www.utm.my/> |
| program_intake | `month` | <https://www.utoronto.ca/> |
| program_intake | `month` | <https://www.utu.fi/en> |
| program_intake | `term` | <https://en.nagoya-u.ac.jp/> |
| program_intake | `term` | <https://en.snu.ac.kr/> |
| program_intake | `term` | <https://tu-dresden.de/> |
| program_intake | `term` | <https://uwaterloo.ca/> |
| program_intake | `term` | <https://www.aalto.fi/en> |
| program_intake | `term` | <https://www.bogazici.edu.tr> |
| program_intake | `term` | <https://www.dcu.ie/> |
| program_intake | `term` | <https://www.global.hokudai.ac.jp/> |
| program_intake | `term` | <https://www.helsinki.fi/en> |
| program_intake | `term` | <https://www.hu-berlin.de/en> |
| program_intake | `term` | <https://www.itu.edu.tr> |
| program_intake | `term` | <https://www.jku.at> |
| program_intake | `term` | <https://www.jyu.fi/en> |
| program_intake | `term` | <https://www.kaist.ac.kr/en/> |
| program_intake | `term` | <https://www.kau.edu.sa> |
| program_intake | `term` | <https://www.kaust.edu.sa> |
| program_intake | `term` | <https://www.keio.ac.jp/en/> |
| program_intake | `term` | <https://www.kfupm.edu.sa> |
| program_intake | `term` | <https://www.kit.edu/english/> |
| program_intake | `term` | <https://www.korea.edu/> |
| program_intake | `term` | <https://www.ksu.edu.sa> |
| program_intake | `term` | <https://www.ku.edu.tr> |
| program_intake | `term` | <https://www.kyoto-u.ac.jp/en> |
| program_intake | `term` | <https://www.kyushu-u.ac.jp/en/> |
| program_intake | `term` | <https://www.lmu.de/en/> |
| program_intake | `term` | <https://www.lut.fi/en> |
| program_intake | `term` | <https://www.mcgill.ca/> |
| program_intake | `term` | <https://www.metu.edu.tr> |
| program_intake | `term` | <https://www.monash.edu.my/> |
| program_intake | `term` | <https://www.monash.edu/> |
| program_intake | `term` | <https://www.ntu.edu.sg> |
| program_intake | `term` | <https://www.nus.edu.sg> |
| program_intake | `term` | <https://www.osaka-u.ac.jp/en> |
| program_intake | `term` | <https://www.oulu.fi/en> |
| program_intake | `term` | <https://www.smu.edu.sg> |
| program_intake | `term` | <https://www.sydney.edu.au/> |
| program_intake | `term` | <https://www.tcd.ie/> |
| program_intake | `term` | <https://www.tohoku.ac.jp/en/> |
| program_intake | `term` | <https://www.tsukuba.ac.jp/en/> |
| program_intake | `term` | <https://www.tu.berlin/en/> |
| program_intake | `term` | <https://www.tugraz.at> |
| program_intake | `term` | <https://www.tum.de/en/> |
| program_intake | `term` | <https://www.tuni.fi/en> |
| program_intake | `term` | <https://www.tuwien.at> |
| program_intake | `term` | <https://www.u-tokyo.ac.jp/en/> |
| program_intake | `term` | <https://www.ualberta.ca/> |
| program_intake | `term` | <https://www.ub.edu> |
| program_intake | `term` | <https://www.ubc.ca/> |
| program_intake | `term` | <https://www.uc3m.es> |
| program_intake | `term` | <https://www.ucc.ie/> |
| program_intake | `term` | <https://www.ucd.ie/> |
| program_intake | `term` | <https://www.ucm.es> |
| program_intake | `term` | <https://www.uef.fi/en> |
| program_intake | `term` | <https://www.uibk.ac.at> |
| program_intake | `term` | <https://www.um.edu.my/> |
| program_intake | `term` | <https://www.uni-freiburg.de/en> |
| program_intake | `term` | <https://www.uni-heidelberg.de/en> |
| program_intake | `term` | <https://www.uni-stuttgart.de/en/> |
| program_intake | `term` | <https://www.unimelb.edu.au/> |
| program_intake | `term` | <https://www.universityofgalway.ie/> |
| program_intake | `term` | <https://www.univie.ac.at> |
| program_intake | `term` | <https://www.unsw.edu.au/> |
| program_intake | `term` | <https://www.upf.edu> |
| program_intake | `term` | <https://www.upm.edu.my/> |
| program_intake | `term` | <https://www.uq.edu.au/> |
| program_intake | `term` | <https://www.utm.my/> |
| program_intake | `term` | <https://www.utoronto.ca/> |
| program_intake | `term` | <https://www.utu.fi/en> |
| program_intake | `year` | <https://en.nagoya-u.ac.jp/> |
| program_intake | `year` | <https://en.snu.ac.kr/> |
| program_intake | `year` | <https://tu-dresden.de/> |
| program_intake | `year` | <https://uwaterloo.ca/> |
| program_intake | `year` | <https://www.aalto.fi/en> |
| program_intake | `year` | <https://www.bogazici.edu.tr> |
| program_intake | `year` | <https://www.dcu.ie/> |
| program_intake | `year` | <https://www.global.hokudai.ac.jp/> |
| program_intake | `year` | <https://www.helsinki.fi/en> |
| program_intake | `year` | <https://www.hu-berlin.de/en> |
| program_intake | `year` | <https://www.itu.edu.tr> |
| program_intake | `year` | <https://www.jku.at> |
| program_intake | `year` | <https://www.jyu.fi/en> |
| program_intake | `year` | <https://www.kaist.ac.kr/en/> |
| program_intake | `year` | <https://www.kau.edu.sa> |
| program_intake | `year` | <https://www.kaust.edu.sa> |
| program_intake | `year` | <https://www.keio.ac.jp/en/> |
| program_intake | `year` | <https://www.kfupm.edu.sa> |
| program_intake | `year` | <https://www.kit.edu/english/> |
| program_intake | `year` | <https://www.korea.edu/> |
| program_intake | `year` | <https://www.ksu.edu.sa> |
| program_intake | `year` | <https://www.ku.edu.tr> |
| program_intake | `year` | <https://www.kyoto-u.ac.jp/en> |
| program_intake | `year` | <https://www.kyushu-u.ac.jp/en/> |
| program_intake | `year` | <https://www.lmu.de/en/> |
| program_intake | `year` | <https://www.lut.fi/en> |
| program_intake | `year` | <https://www.mcgill.ca/> |
| program_intake | `year` | <https://www.metu.edu.tr> |
| program_intake | `year` | <https://www.monash.edu.my/> |
| program_intake | `year` | <https://www.monash.edu/> |
| program_intake | `year` | <https://www.ntu.edu.sg> |
| program_intake | `year` | <https://www.nus.edu.sg> |
| program_intake | `year` | <https://www.osaka-u.ac.jp/en> |
| program_intake | `year` | <https://www.oulu.fi/en> |
| program_intake | `year` | <https://www.smu.edu.sg> |
| program_intake | `year` | <https://www.sydney.edu.au/> |
| program_intake | `year` | <https://www.tcd.ie/> |
| program_intake | `year` | <https://www.tohoku.ac.jp/en/> |
| program_intake | `year` | <https://www.tsukuba.ac.jp/en/> |
| program_intake | `year` | <https://www.tu.berlin/en/> |
| program_intake | `year` | <https://www.tugraz.at> |
| program_intake | `year` | <https://www.tum.de/en/> |
| program_intake | `year` | <https://www.tuni.fi/en> |
| program_intake | `year` | <https://www.tuwien.at> |
| program_intake | `year` | <https://www.u-tokyo.ac.jp/en/> |
| program_intake | `year` | <https://www.ualberta.ca/> |
| program_intake | `year` | <https://www.ub.edu> |
| program_intake | `year` | <https://www.ubc.ca/> |
| program_intake | `year` | <https://www.uc3m.es> |
| program_intake | `year` | <https://www.ucc.ie/> |
| program_intake | `year` | <https://www.ucd.ie/> |
| program_intake | `year` | <https://www.ucm.es> |
| program_intake | `year` | <https://www.uef.fi/en> |
| program_intake | `year` | <https://www.uibk.ac.at> |
| program_intake | `year` | <https://www.um.edu.my/> |
| program_intake | `year` | <https://www.uni-freiburg.de/en> |
| program_intake | `year` | <https://www.uni-heidelberg.de/en> |
| program_intake | `year` | <https://www.uni-stuttgart.de/en/> |
| program_intake | `year` | <https://www.unimelb.edu.au/> |
| program_intake | `year` | <https://www.universityofgalway.ie/> |
| program_intake | `year` | <https://www.univie.ac.at> |
| program_intake | `year` | <https://www.unsw.edu.au/> |
| program_intake | `year` | <https://www.upf.edu> |
| program_intake | `year` | <https://www.upm.edu.my/> |
| program_intake | `year` | <https://www.uq.edu.au/> |
| program_intake | `year` | <https://www.utm.my/> |
| program_intake | `year` | <https://www.utoronto.ca/> |
| program_intake | `year` | <https://www.utu.fi/en> |
| program_requirement | `cgpa_scale` | <https://en.nagoya-u.ac.jp/> |
| program_requirement | `cgpa_scale` | <https://en.snu.ac.kr/> |
| program_requirement | `cgpa_scale` | <https://tu-dresden.de/> |
| program_requirement | `cgpa_scale` | <https://uwaterloo.ca/> |
| program_requirement | `cgpa_scale` | <https://www.aalto.fi/en> |
| program_requirement | `cgpa_scale` | <https://www.bogazici.edu.tr> |
| program_requirement | `cgpa_scale` | <https://www.dcu.ie/> |
| program_requirement | `cgpa_scale` | <https://www.global.hokudai.ac.jp/> |
| program_requirement | `cgpa_scale` | <https://www.helsinki.fi/en> |
| program_requirement | `cgpa_scale` | <https://www.hu-berlin.de/en> |
| program_requirement | `cgpa_scale` | <https://www.itu.edu.tr> |
| program_requirement | `cgpa_scale` | <https://www.jku.at> |
| program_requirement | `cgpa_scale` | <https://www.jyu.fi/en> |
| program_requirement | `cgpa_scale` | <https://www.kaist.ac.kr/en/> |
| program_requirement | `cgpa_scale` | <https://www.kau.edu.sa> |
| program_requirement | `cgpa_scale` | <https://www.kaust.edu.sa> |
| program_requirement | `cgpa_scale` | <https://www.keio.ac.jp/en/> |
| program_requirement | `cgpa_scale` | <https://www.kfupm.edu.sa> |
| program_requirement | `cgpa_scale` | <https://www.kit.edu/english/> |
| program_requirement | `cgpa_scale` | <https://www.korea.edu/> |
| program_requirement | `cgpa_scale` | <https://www.ksu.edu.sa> |
| program_requirement | `cgpa_scale` | <https://www.ku.edu.tr> |
| program_requirement | `cgpa_scale` | <https://www.kyoto-u.ac.jp/en> |
| program_requirement | `cgpa_scale` | <https://www.kyushu-u.ac.jp/en/> |
| program_requirement | `cgpa_scale` | <https://www.lmu.de/en/> |
| program_requirement | `cgpa_scale` | <https://www.lut.fi/en> |
| program_requirement | `cgpa_scale` | <https://www.mcgill.ca/> |
| program_requirement | `cgpa_scale` | <https://www.metu.edu.tr> |
| program_requirement | `cgpa_scale` | <https://www.monash.edu.my/> |
| program_requirement | `cgpa_scale` | <https://www.monash.edu/> |
| program_requirement | `cgpa_scale` | <https://www.ntu.edu.sg> |
| program_requirement | `cgpa_scale` | <https://www.nus.edu.sg> |
| program_requirement | `cgpa_scale` | <https://www.osaka-u.ac.jp/en> |
| program_requirement | `cgpa_scale` | <https://www.oulu.fi/en> |
| program_requirement | `cgpa_scale` | <https://www.smu.edu.sg> |
| program_requirement | `cgpa_scale` | <https://www.sydney.edu.au/> |
| program_requirement | `cgpa_scale` | <https://www.tcd.ie/> |
| program_requirement | `cgpa_scale` | <https://www.tohoku.ac.jp/en/> |
| program_requirement | `cgpa_scale` | <https://www.tsukuba.ac.jp/en/> |
| program_requirement | `cgpa_scale` | <https://www.tu.berlin/en/> |
| program_requirement | `cgpa_scale` | <https://www.tugraz.at> |
| program_requirement | `cgpa_scale` | <https://www.tum.de/en/> |
| program_requirement | `cgpa_scale` | <https://www.tuni.fi/en> |
| program_requirement | `cgpa_scale` | <https://www.tuwien.at> |
| program_requirement | `cgpa_scale` | <https://www.u-tokyo.ac.jp/en/> |
| program_requirement | `cgpa_scale` | <https://www.ualberta.ca/> |
| program_requirement | `cgpa_scale` | <https://www.ub.edu> |
| program_requirement | `cgpa_scale` | <https://www.ubc.ca/> |
| program_requirement | `cgpa_scale` | <https://www.uc3m.es> |
| program_requirement | `cgpa_scale` | <https://www.ucc.ie/> |
| program_requirement | `cgpa_scale` | <https://www.ucd.ie/> |
| program_requirement | `cgpa_scale` | <https://www.ucm.es> |
| program_requirement | `cgpa_scale` | <https://www.uef.fi/en> |
| program_requirement | `cgpa_scale` | <https://www.uibk.ac.at> |
| program_requirement | `cgpa_scale` | <https://www.um.edu.my/> |
| program_requirement | `cgpa_scale` | <https://www.uni-freiburg.de/en> |
| program_requirement | `cgpa_scale` | <https://www.uni-heidelberg.de/en> |
| program_requirement | `cgpa_scale` | <https://www.uni-stuttgart.de/en/> |
| program_requirement | `cgpa_scale` | <https://www.unimelb.edu.au/> |
| program_requirement | `cgpa_scale` | <https://www.universityofgalway.ie/> |
| program_requirement | `cgpa_scale` | <https://www.univie.ac.at> |
| program_requirement | `cgpa_scale` | <https://www.unsw.edu.au/> |
| program_requirement | `cgpa_scale` | <https://www.upf.edu> |
| program_requirement | `cgpa_scale` | <https://www.upm.edu.my/> |
| program_requirement | `cgpa_scale` | <https://www.uq.edu.au/> |
| program_requirement | `cgpa_scale` | <https://www.utm.my/> |
| program_requirement | `cgpa_scale` | <https://www.utoronto.ca/> |
| program_requirement | `cgpa_scale` | <https://www.utu.fi/en> |
| program_requirement | `ielts_overall` | <https://en.nagoya-u.ac.jp/> |
| program_requirement | `ielts_overall` | <https://en.snu.ac.kr/> |
| program_requirement | `ielts_overall` | <https://tu-dresden.de/> |
| program_requirement | `ielts_overall` | <https://uwaterloo.ca/> |
| program_requirement | `ielts_overall` | <https://www.aalto.fi/en> |
| program_requirement | `ielts_overall` | <https://www.bogazici.edu.tr> |
| program_requirement | `ielts_overall` | <https://www.dcu.ie/> |
| program_requirement | `ielts_overall` | <https://www.global.hokudai.ac.jp/> |
| program_requirement | `ielts_overall` | <https://www.helsinki.fi/en> |
| program_requirement | `ielts_overall` | <https://www.hu-berlin.de/en> |
| program_requirement | `ielts_overall` | <https://www.itu.edu.tr> |
| program_requirement | `ielts_overall` | <https://www.jku.at> |
| program_requirement | `ielts_overall` | <https://www.jyu.fi/en> |
| program_requirement | `ielts_overall` | <https://www.kaist.ac.kr/en/> |
| program_requirement | `ielts_overall` | <https://www.kau.edu.sa> |
| program_requirement | `ielts_overall` | <https://www.kaust.edu.sa> |
| program_requirement | `ielts_overall` | <https://www.keio.ac.jp/en/> |
| program_requirement | `ielts_overall` | <https://www.kfupm.edu.sa> |
| program_requirement | `ielts_overall` | <https://www.kit.edu/english/> |
| program_requirement | `ielts_overall` | <https://www.korea.edu/> |
| program_requirement | `ielts_overall` | <https://www.ksu.edu.sa> |
| program_requirement | `ielts_overall` | <https://www.ku.edu.tr> |
| program_requirement | `ielts_overall` | <https://www.kyoto-u.ac.jp/en> |
| program_requirement | `ielts_overall` | <https://www.kyushu-u.ac.jp/en/> |
| program_requirement | `ielts_overall` | <https://www.lmu.de/en/> |
| program_requirement | `ielts_overall` | <https://www.lut.fi/en> |
| program_requirement | `ielts_overall` | <https://www.mcgill.ca/> |
| program_requirement | `ielts_overall` | <https://www.metu.edu.tr> |
| program_requirement | `ielts_overall` | <https://www.monash.edu.my/> |
| program_requirement | `ielts_overall` | <https://www.monash.edu/> |
| program_requirement | `ielts_overall` | <https://www.ntu.edu.sg> |
| program_requirement | `ielts_overall` | <https://www.nus.edu.sg> |
| program_requirement | `ielts_overall` | <https://www.osaka-u.ac.jp/en> |
| program_requirement | `ielts_overall` | <https://www.oulu.fi/en> |
| program_requirement | `ielts_overall` | <https://www.smu.edu.sg> |
| program_requirement | `ielts_overall` | <https://www.sydney.edu.au/> |
| program_requirement | `ielts_overall` | <https://www.tcd.ie/> |
| program_requirement | `ielts_overall` | <https://www.tohoku.ac.jp/en/> |
| program_requirement | `ielts_overall` | <https://www.tsukuba.ac.jp/en/> |
| program_requirement | `ielts_overall` | <https://www.tu.berlin/en/> |
| program_requirement | `ielts_overall` | <https://www.tugraz.at> |
| program_requirement | `ielts_overall` | <https://www.tum.de/en/> |
| program_requirement | `ielts_overall` | <https://www.tuni.fi/en> |
| program_requirement | `ielts_overall` | <https://www.tuwien.at> |
| program_requirement | `ielts_overall` | <https://www.u-tokyo.ac.jp/en/> |
| program_requirement | `ielts_overall` | <https://www.ualberta.ca/> |
| program_requirement | `ielts_overall` | <https://www.ub.edu> |
| program_requirement | `ielts_overall` | <https://www.ubc.ca/> |
| program_requirement | `ielts_overall` | <https://www.uc3m.es> |
| program_requirement | `ielts_overall` | <https://www.ucc.ie/> |
| program_requirement | `ielts_overall` | <https://www.ucd.ie/> |
| program_requirement | `ielts_overall` | <https://www.ucm.es> |
| program_requirement | `ielts_overall` | <https://www.uef.fi/en> |
| program_requirement | `ielts_overall` | <https://www.uibk.ac.at> |
| program_requirement | `ielts_overall` | <https://www.um.edu.my/> |
| program_requirement | `ielts_overall` | <https://www.uni-freiburg.de/en> |
| program_requirement | `ielts_overall` | <https://www.uni-heidelberg.de/en> |
| program_requirement | `ielts_overall` | <https://www.uni-stuttgart.de/en/> |
| program_requirement | `ielts_overall` | <https://www.unimelb.edu.au/> |
| program_requirement | `ielts_overall` | <https://www.universityofgalway.ie/> |
| program_requirement | `ielts_overall` | <https://www.univie.ac.at> |
| program_requirement | `ielts_overall` | <https://www.unsw.edu.au/> |
| program_requirement | `ielts_overall` | <https://www.upf.edu> |
| program_requirement | `ielts_overall` | <https://www.upm.edu.my/> |
| program_requirement | `ielts_overall` | <https://www.uq.edu.au/> |
| program_requirement | `ielts_overall` | <https://www.utm.my/> |
| program_requirement | `ielts_overall` | <https://www.utoronto.ca/> |
| program_requirement | `ielts_overall` | <https://www.utu.fi/en> |
| program_requirement | `min_cgpa` | <https://en.nagoya-u.ac.jp/> |
| program_requirement | `min_cgpa` | <https://en.snu.ac.kr/> |
| program_requirement | `min_cgpa` | <https://tu-dresden.de/> |
| program_requirement | `min_cgpa` | <https://uwaterloo.ca/> |
| program_requirement | `min_cgpa` | <https://www.aalto.fi/en> |
| program_requirement | `min_cgpa` | <https://www.bogazici.edu.tr> |
| program_requirement | `min_cgpa` | <https://www.dcu.ie/> |
| program_requirement | `min_cgpa` | <https://www.global.hokudai.ac.jp/> |
| program_requirement | `min_cgpa` | <https://www.helsinki.fi/en> |
| program_requirement | `min_cgpa` | <https://www.hu-berlin.de/en> |
| program_requirement | `min_cgpa` | <https://www.itu.edu.tr> |
| program_requirement | `min_cgpa` | <https://www.jku.at> |
| program_requirement | `min_cgpa` | <https://www.jyu.fi/en> |
| program_requirement | `min_cgpa` | <https://www.kaist.ac.kr/en/> |
| program_requirement | `min_cgpa` | <https://www.kau.edu.sa> |
| program_requirement | `min_cgpa` | <https://www.kaust.edu.sa> |
| program_requirement | `min_cgpa` | <https://www.keio.ac.jp/en/> |
| program_requirement | `min_cgpa` | <https://www.kfupm.edu.sa> |
| program_requirement | `min_cgpa` | <https://www.kit.edu/english/> |
| program_requirement | `min_cgpa` | <https://www.korea.edu/> |
| program_requirement | `min_cgpa` | <https://www.ksu.edu.sa> |
| program_requirement | `min_cgpa` | <https://www.ku.edu.tr> |
| program_requirement | `min_cgpa` | <https://www.kyoto-u.ac.jp/en> |
| program_requirement | `min_cgpa` | <https://www.kyushu-u.ac.jp/en/> |
| program_requirement | `min_cgpa` | <https://www.lmu.de/en/> |
| program_requirement | `min_cgpa` | <https://www.lut.fi/en> |
| program_requirement | `min_cgpa` | <https://www.mcgill.ca/> |
| program_requirement | `min_cgpa` | <https://www.metu.edu.tr> |
| program_requirement | `min_cgpa` | <https://www.monash.edu.my/> |
| program_requirement | `min_cgpa` | <https://www.monash.edu/> |
| program_requirement | `min_cgpa` | <https://www.ntu.edu.sg> |
| program_requirement | `min_cgpa` | <https://www.nus.edu.sg> |
| program_requirement | `min_cgpa` | <https://www.osaka-u.ac.jp/en> |
| program_requirement | `min_cgpa` | <https://www.oulu.fi/en> |
| program_requirement | `min_cgpa` | <https://www.smu.edu.sg> |
| program_requirement | `min_cgpa` | <https://www.sydney.edu.au/> |
| program_requirement | `min_cgpa` | <https://www.tcd.ie/> |
| program_requirement | `min_cgpa` | <https://www.tohoku.ac.jp/en/> |
| program_requirement | `min_cgpa` | <https://www.tsukuba.ac.jp/en/> |
| program_requirement | `min_cgpa` | <https://www.tu.berlin/en/> |
| program_requirement | `min_cgpa` | <https://www.tugraz.at> |
| program_requirement | `min_cgpa` | <https://www.tum.de/en/> |
| program_requirement | `min_cgpa` | <https://www.tuni.fi/en> |
| program_requirement | `min_cgpa` | <https://www.tuwien.at> |
| program_requirement | `min_cgpa` | <https://www.u-tokyo.ac.jp/en/> |
| program_requirement | `min_cgpa` | <https://www.ualberta.ca/> |
| program_requirement | `min_cgpa` | <https://www.ub.edu> |
| program_requirement | `min_cgpa` | <https://www.ubc.ca/> |
| program_requirement | `min_cgpa` | <https://www.uc3m.es> |
| program_requirement | `min_cgpa` | <https://www.ucc.ie/> |
| program_requirement | `min_cgpa` | <https://www.ucd.ie/> |
| program_requirement | `min_cgpa` | <https://www.ucm.es> |
| program_requirement | `min_cgpa` | <https://www.uef.fi/en> |
| program_requirement | `min_cgpa` | <https://www.uibk.ac.at> |
| program_requirement | `min_cgpa` | <https://www.um.edu.my/> |
| program_requirement | `min_cgpa` | <https://www.uni-freiburg.de/en> |
| program_requirement | `min_cgpa` | <https://www.uni-heidelberg.de/en> |
| program_requirement | `min_cgpa` | <https://www.uni-stuttgart.de/en/> |
| program_requirement | `min_cgpa` | <https://www.unimelb.edu.au/> |
| program_requirement | `min_cgpa` | <https://www.universityofgalway.ie/> |
| program_requirement | `min_cgpa` | <https://www.univie.ac.at> |
| program_requirement | `min_cgpa` | <https://www.unsw.edu.au/> |
| program_requirement | `min_cgpa` | <https://www.upf.edu> |
| program_requirement | `min_cgpa` | <https://www.upm.edu.my/> |
| program_requirement | `min_cgpa` | <https://www.uq.edu.au/> |
| program_requirement | `min_cgpa` | <https://www.utm.my/> |
| program_requirement | `min_cgpa` | <https://www.utoronto.ca/> |
| program_requirement | `min_cgpa` | <https://www.utu.fi/en> |
| program_requirement | `min_qualification` | <https://en.nagoya-u.ac.jp/> |
| program_requirement | `min_qualification` | <https://en.snu.ac.kr/> |
| program_requirement | `min_qualification` | <https://tu-dresden.de/> |
| program_requirement | `min_qualification` | <https://uwaterloo.ca/> |
| program_requirement | `min_qualification` | <https://www.aalto.fi/en> |
| program_requirement | `min_qualification` | <https://www.bogazici.edu.tr> |
| program_requirement | `min_qualification` | <https://www.dcu.ie/> |
| program_requirement | `min_qualification` | <https://www.global.hokudai.ac.jp/> |
| program_requirement | `min_qualification` | <https://www.helsinki.fi/en> |
| program_requirement | `min_qualification` | <https://www.hu-berlin.de/en> |
| program_requirement | `min_qualification` | <https://www.itu.edu.tr> |
| program_requirement | `min_qualification` | <https://www.jku.at> |
| program_requirement | `min_qualification` | <https://www.jyu.fi/en> |
| program_requirement | `min_qualification` | <https://www.kaist.ac.kr/en/> |
| program_requirement | `min_qualification` | <https://www.kau.edu.sa> |
| program_requirement | `min_qualification` | <https://www.kaust.edu.sa> |
| program_requirement | `min_qualification` | <https://www.keio.ac.jp/en/> |
| program_requirement | `min_qualification` | <https://www.kfupm.edu.sa> |
| program_requirement | `min_qualification` | <https://www.kit.edu/english/> |
| program_requirement | `min_qualification` | <https://www.korea.edu/> |
| program_requirement | `min_qualification` | <https://www.ksu.edu.sa> |
| program_requirement | `min_qualification` | <https://www.ku.edu.tr> |
| program_requirement | `min_qualification` | <https://www.kyoto-u.ac.jp/en> |
| program_requirement | `min_qualification` | <https://www.kyushu-u.ac.jp/en/> |
| program_requirement | `min_qualification` | <https://www.lmu.de/en/> |
| program_requirement | `min_qualification` | <https://www.lut.fi/en> |
| program_requirement | `min_qualification` | <https://www.mcgill.ca/> |
| program_requirement | `min_qualification` | <https://www.metu.edu.tr> |
| program_requirement | `min_qualification` | <https://www.monash.edu.my/> |
| program_requirement | `min_qualification` | <https://www.monash.edu/> |
| program_requirement | `min_qualification` | <https://www.ntu.edu.sg> |
| program_requirement | `min_qualification` | <https://www.nus.edu.sg> |
| program_requirement | `min_qualification` | <https://www.osaka-u.ac.jp/en> |
| program_requirement | `min_qualification` | <https://www.oulu.fi/en> |
| program_requirement | `min_qualification` | <https://www.smu.edu.sg> |
| program_requirement | `min_qualification` | <https://www.sydney.edu.au/> |
| program_requirement | `min_qualification` | <https://www.tcd.ie/> |
| program_requirement | `min_qualification` | <https://www.tohoku.ac.jp/en/> |
| program_requirement | `min_qualification` | <https://www.tsukuba.ac.jp/en/> |
| program_requirement | `min_qualification` | <https://www.tu.berlin/en/> |
| program_requirement | `min_qualification` | <https://www.tugraz.at> |
| program_requirement | `min_qualification` | <https://www.tum.de/en/> |
| program_requirement | `min_qualification` | <https://www.tuni.fi/en> |
| program_requirement | `min_qualification` | <https://www.tuwien.at> |
| program_requirement | `min_qualification` | <https://www.u-tokyo.ac.jp/en/> |
| program_requirement | `min_qualification` | <https://www.ualberta.ca/> |
| program_requirement | `min_qualification` | <https://www.ub.edu> |
| program_requirement | `min_qualification` | <https://www.ubc.ca/> |
| program_requirement | `min_qualification` | <https://www.uc3m.es> |
| program_requirement | `min_qualification` | <https://www.ucc.ie/> |
| program_requirement | `min_qualification` | <https://www.ucd.ie/> |
| program_requirement | `min_qualification` | <https://www.ucm.es> |
| program_requirement | `min_qualification` | <https://www.uef.fi/en> |
| program_requirement | `min_qualification` | <https://www.uibk.ac.at> |
| program_requirement | `min_qualification` | <https://www.um.edu.my/> |
| program_requirement | `min_qualification` | <https://www.uni-freiburg.de/en> |
| program_requirement | `min_qualification` | <https://www.uni-heidelberg.de/en> |
| program_requirement | `min_qualification` | <https://www.uni-stuttgart.de/en/> |
| program_requirement | `min_qualification` | <https://www.unimelb.edu.au/> |
| program_requirement | `min_qualification` | <https://www.universityofgalway.ie/> |
| program_requirement | `min_qualification` | <https://www.univie.ac.at> |
| program_requirement | `min_qualification` | <https://www.unsw.edu.au/> |
| program_requirement | `min_qualification` | <https://www.upf.edu> |
| program_requirement | `min_qualification` | <https://www.upm.edu.my/> |
| program_requirement | `min_qualification` | <https://www.uq.edu.au/> |
| program_requirement | `min_qualification` | <https://www.utm.my/> |
| program_requirement | `min_qualification` | <https://www.utoronto.ca/> |
| program_requirement | `min_qualification` | <https://www.utu.fi/en> |
| program_requirement | `toefl_ibt` | <https://en.nagoya-u.ac.jp/> |
| program_requirement | `toefl_ibt` | <https://en.snu.ac.kr/> |
| program_requirement | `toefl_ibt` | <https://tu-dresden.de/> |
| program_requirement | `toefl_ibt` | <https://uwaterloo.ca/> |
| program_requirement | `toefl_ibt` | <https://www.aalto.fi/en> |
| program_requirement | `toefl_ibt` | <https://www.bogazici.edu.tr> |
| program_requirement | `toefl_ibt` | <https://www.dcu.ie/> |
| program_requirement | `toefl_ibt` | <https://www.global.hokudai.ac.jp/> |
| program_requirement | `toefl_ibt` | <https://www.helsinki.fi/en> |
| program_requirement | `toefl_ibt` | <https://www.hu-berlin.de/en> |
| program_requirement | `toefl_ibt` | <https://www.itu.edu.tr> |
| program_requirement | `toefl_ibt` | <https://www.jku.at> |
| program_requirement | `toefl_ibt` | <https://www.jyu.fi/en> |
| program_requirement | `toefl_ibt` | <https://www.kaist.ac.kr/en/> |
| program_requirement | `toefl_ibt` | <https://www.kau.edu.sa> |
| program_requirement | `toefl_ibt` | <https://www.kaust.edu.sa> |
| program_requirement | `toefl_ibt` | <https://www.keio.ac.jp/en/> |
| program_requirement | `toefl_ibt` | <https://www.kfupm.edu.sa> |
| program_requirement | `toefl_ibt` | <https://www.kit.edu/english/> |
| program_requirement | `toefl_ibt` | <https://www.korea.edu/> |
| program_requirement | `toefl_ibt` | <https://www.ksu.edu.sa> |
| program_requirement | `toefl_ibt` | <https://www.ku.edu.tr> |
| program_requirement | `toefl_ibt` | <https://www.kyoto-u.ac.jp/en> |
| program_requirement | `toefl_ibt` | <https://www.kyushu-u.ac.jp/en/> |
| program_requirement | `toefl_ibt` | <https://www.lmu.de/en/> |
| program_requirement | `toefl_ibt` | <https://www.lut.fi/en> |
| program_requirement | `toefl_ibt` | <https://www.mcgill.ca/> |
| program_requirement | `toefl_ibt` | <https://www.metu.edu.tr> |
| program_requirement | `toefl_ibt` | <https://www.monash.edu.my/> |
| program_requirement | `toefl_ibt` | <https://www.monash.edu/> |
| program_requirement | `toefl_ibt` | <https://www.ntu.edu.sg> |
| program_requirement | `toefl_ibt` | <https://www.nus.edu.sg> |
| program_requirement | `toefl_ibt` | <https://www.osaka-u.ac.jp/en> |
| program_requirement | `toefl_ibt` | <https://www.oulu.fi/en> |
| program_requirement | `toefl_ibt` | <https://www.smu.edu.sg> |
| program_requirement | `toefl_ibt` | <https://www.sydney.edu.au/> |
| program_requirement | `toefl_ibt` | <https://www.tcd.ie/> |
| program_requirement | `toefl_ibt` | <https://www.tohoku.ac.jp/en/> |
| program_requirement | `toefl_ibt` | <https://www.tsukuba.ac.jp/en/> |
| program_requirement | `toefl_ibt` | <https://www.tu.berlin/en/> |
| program_requirement | `toefl_ibt` | <https://www.tugraz.at> |
| program_requirement | `toefl_ibt` | <https://www.tum.de/en/> |
| program_requirement | `toefl_ibt` | <https://www.tuni.fi/en> |
| program_requirement | `toefl_ibt` | <https://www.tuwien.at> |
| program_requirement | `toefl_ibt` | <https://www.u-tokyo.ac.jp/en/> |
| program_requirement | `toefl_ibt` | <https://www.ualberta.ca/> |
| program_requirement | `toefl_ibt` | <https://www.ub.edu> |
| program_requirement | `toefl_ibt` | <https://www.ubc.ca/> |
| program_requirement | `toefl_ibt` | <https://www.uc3m.es> |
| program_requirement | `toefl_ibt` | <https://www.ucc.ie/> |
| program_requirement | `toefl_ibt` | <https://www.ucd.ie/> |
| program_requirement | `toefl_ibt` | <https://www.ucm.es> |
| program_requirement | `toefl_ibt` | <https://www.uef.fi/en> |
| program_requirement | `toefl_ibt` | <https://www.uibk.ac.at> |
| program_requirement | `toefl_ibt` | <https://www.um.edu.my/> |
| program_requirement | `toefl_ibt` | <https://www.uni-freiburg.de/en> |
| program_requirement | `toefl_ibt` | <https://www.uni-heidelberg.de/en> |
| program_requirement | `toefl_ibt` | <https://www.uni-stuttgart.de/en/> |
| program_requirement | `toefl_ibt` | <https://www.unimelb.edu.au/> |
| program_requirement | `toefl_ibt` | <https://www.universityofgalway.ie/> |
| program_requirement | `toefl_ibt` | <https://www.univie.ac.at> |
| program_requirement | `toefl_ibt` | <https://www.unsw.edu.au/> |
| program_requirement | `toefl_ibt` | <https://www.upf.edu> |
| program_requirement | `toefl_ibt` | <https://www.upm.edu.my/> |
| program_requirement | `toefl_ibt` | <https://www.uq.edu.au/> |
| program_requirement | `toefl_ibt` | <https://www.utm.my/> |
| program_requirement | `toefl_ibt` | <https://www.utoronto.ca/> |
| program_requirement | `toefl_ibt` | <https://www.utu.fi/en> |
| scholarship | `amount_value` | <https://studyinsaudi.moe.gov.sa/> |
| scholarship | `amount_value` | <https://www.studyinfinland.fi/> |
| scholarship | `amount_value` | <https://www.turkiyeburslari.gov.tr/> |
| scholarship | `country` | <https://studyinsaudi.moe.gov.sa/> |
| scholarship | `country` | <https://www.studyinfinland.fi/> |
| scholarship | `country` | <https://www.turkiyeburslari.gov.tr/> |
| scholarship | `currency` | <https://www.studyinfinland.fi/> |
| scholarship | `currency` | <https://www.turkiyeburslari.gov.tr/> |
| university | `city` | <https://en.nagoya-u.ac.jp/> |
| university | `city` | <https://en.snu.ac.kr/> |
| university | `city` | <https://tu-dresden.de/> |
| university | `city` | <https://uwaterloo.ca/> |
| university | `city` | <https://www.aalto.fi/en> |
| university | `city` | <https://www.bogazici.edu.tr> |
| university | `city` | <https://www.dcu.ie/> |
| university | `city` | <https://www.global.hokudai.ac.jp/> |
| university | `city` | <https://www.helsinki.fi/en> |
| university | `city` | <https://www.hu-berlin.de/en> |
| university | `city` | <https://www.itu.edu.tr> |
| university | `city` | <https://www.jku.at> |
| university | `city` | <https://www.jyu.fi/en> |
| university | `city` | <https://www.kaist.ac.kr/en/> |
| university | `city` | <https://www.kau.edu.sa> |
| university | `city` | <https://www.kaust.edu.sa> |
| university | `city` | <https://www.keio.ac.jp/en/> |
| university | `city` | <https://www.kfupm.edu.sa> |
| university | `city` | <https://www.kit.edu/english/> |
| university | `city` | <https://www.korea.edu/> |
| university | `city` | <https://www.ksu.edu.sa> |
| university | `city` | <https://www.ku.edu.tr> |
| university | `city` | <https://www.kyoto-u.ac.jp/en> |
| university | `city` | <https://www.kyushu-u.ac.jp/en/> |
| university | `city` | <https://www.lmu.de/en/> |
| university | `city` | <https://www.lut.fi/en> |
| university | `city` | <https://www.mcgill.ca/> |
| university | `city` | <https://www.metu.edu.tr> |
| university | `city` | <https://www.monash.edu.my/> |
| university | `city` | <https://www.monash.edu/> |
| university | `city` | <https://www.ntu.edu.sg> |
| university | `city` | <https://www.nus.edu.sg> |
| university | `city` | <https://www.osaka-u.ac.jp/en> |
| university | `city` | <https://www.oulu.fi/en> |
| university | `city` | <https://www.smu.edu.sg> |
| university | `city` | <https://www.sydney.edu.au/> |
| university | `city` | <https://www.tcd.ie/> |
| university | `city` | <https://www.tohoku.ac.jp/en/> |
| university | `city` | <https://www.tsukuba.ac.jp/en/> |
| university | `city` | <https://www.tu.berlin/en/> |
| university | `city` | <https://www.tugraz.at> |
| university | `city` | <https://www.tuni.fi/en> |
| university | `city` | <https://www.tuwien.at> |
| university | `city` | <https://www.u-tokyo.ac.jp/en/> |
| university | `city` | <https://www.ualberta.ca/> |
| university | `city` | <https://www.ub.edu> |
| university | `city` | <https://www.ubc.ca/> |
| university | `city` | <https://www.uc3m.es> |
| university | `city` | <https://www.ucc.ie/> |
| university | `city` | <https://www.ucd.ie/> |
| university | `city` | <https://www.ucm.es> |
| university | `city` | <https://www.uef.fi/en> |
| university | `city` | <https://www.uibk.ac.at> |
| university | `city` | <https://www.um.edu.my/> |
| university | `city` | <https://www.uni-freiburg.de/en> |
| university | `city` | <https://www.uni-heidelberg.de/en> |
| university | `city` | <https://www.uni-stuttgart.de/en/> |
| university | `city` | <https://www.unimelb.edu.au/> |
| university | `city` | <https://www.universityofgalway.ie/> |
| university | `city` | <https://www.univie.ac.at> |
| university | `city` | <https://www.unsw.edu.au/> |
| university | `city` | <https://www.upf.edu> |
| university | `city` | <https://www.upm.edu.my/> |
| university | `city` | <https://www.uq.edu.au/> |
| university | `city` | <https://www.utm.my/> |
| university | `city` | <https://www.utoronto.ca/> |
| university | `city` | <https://www.utu.fi/en> |
| university | `country` | <https://en.nagoya-u.ac.jp/> |
| university | `country` | <https://en.snu.ac.kr/> |
| university | `country` | <https://tu-dresden.de/> |
| university | `country` | <https://uwaterloo.ca/> |
| university | `country` | <https://www.aalto.fi/en> |
| university | `country` | <https://www.bogazici.edu.tr> |
| university | `country` | <https://www.dcu.ie/> |
| university | `country` | <https://www.global.hokudai.ac.jp/> |
| university | `country` | <https://www.helsinki.fi/en> |
| university | `country` | <https://www.hu-berlin.de/en> |
| university | `country` | <https://www.itu.edu.tr> |
| university | `country` | <https://www.jku.at> |
| university | `country` | <https://www.jyu.fi/en> |
| university | `country` | <https://www.kaist.ac.kr/en/> |
| university | `country` | <https://www.kau.edu.sa> |
| university | `country` | <https://www.kaust.edu.sa> |
| university | `country` | <https://www.keio.ac.jp/en/> |
| university | `country` | <https://www.kfupm.edu.sa> |
| university | `country` | <https://www.kit.edu/english/> |
| university | `country` | <https://www.korea.edu/> |
| university | `country` | <https://www.ksu.edu.sa> |
| university | `country` | <https://www.ku.edu.tr> |
| university | `country` | <https://www.kyoto-u.ac.jp/en> |
| university | `country` | <https://www.kyushu-u.ac.jp/en/> |
| university | `country` | <https://www.lmu.de/en/> |
| university | `country` | <https://www.lut.fi/en> |
| university | `country` | <https://www.mcgill.ca/> |
| university | `country` | <https://www.metu.edu.tr> |
| university | `country` | <https://www.monash.edu.my/> |
| university | `country` | <https://www.monash.edu/> |
| university | `country` | <https://www.ntu.edu.sg> |
| university | `country` | <https://www.nus.edu.sg> |
| university | `country` | <https://www.osaka-u.ac.jp/en> |
| university | `country` | <https://www.oulu.fi/en> |
| university | `country` | <https://www.smu.edu.sg> |
| university | `country` | <https://www.sydney.edu.au/> |
| university | `country` | <https://www.tcd.ie/> |
| university | `country` | <https://www.tohoku.ac.jp/en/> |
| university | `country` | <https://www.tsukuba.ac.jp/en/> |
| university | `country` | <https://www.tu.berlin/en/> |
| university | `country` | <https://www.tugraz.at> |
| university | `country` | <https://www.tuni.fi/en> |
| university | `country` | <https://www.tuwien.at> |
| university | `country` | <https://www.u-tokyo.ac.jp/en/> |
| university | `country` | <https://www.ualberta.ca/> |
| university | `country` | <https://www.ub.edu> |
| university | `country` | <https://www.ubc.ca/> |
| university | `country` | <https://www.uc3m.es> |
| university | `country` | <https://www.ucc.ie/> |
| university | `country` | <https://www.ucd.ie/> |
| university | `country` | <https://www.ucm.es> |
| university | `country` | <https://www.uef.fi/en> |
| university | `country` | <https://www.uibk.ac.at> |
| university | `country` | <https://www.um.edu.my/> |
| university | `country` | <https://www.uni-freiburg.de/en> |
| university | `country` | <https://www.uni-heidelberg.de/en> |
| university | `country` | <https://www.uni-stuttgart.de/en/> |
| university | `country` | <https://www.unimelb.edu.au/> |
| university | `country` | <https://www.universityofgalway.ie/> |
| university | `country` | <https://www.univie.ac.at> |
| university | `country` | <https://www.unsw.edu.au/> |
| university | `country` | <https://www.upf.edu> |
| university | `country` | <https://www.upm.edu.my/> |
| university | `country` | <https://www.uq.edu.au/> |
| university | `country` | <https://www.utm.my/> |
| university | `country` | <https://www.utoronto.ca/> |
| university | `country` | <https://www.utu.fi/en> |
| university | `established_year` | <https://en.nagoya-u.ac.jp/> |
| university | `established_year` | <https://en.snu.ac.kr/> |
| university | `established_year` | <https://tu-dresden.de/> |
| university | `established_year` | <https://uwaterloo.ca/> |
| university | `established_year` | <https://www.aalto.fi/en> |
| university | `established_year` | <https://www.bogazici.edu.tr> |
| university | `established_year` | <https://www.dcu.ie/> |
| university | `established_year` | <https://www.global.hokudai.ac.jp/> |
| university | `established_year` | <https://www.helsinki.fi/en> |
| university | `established_year` | <https://www.hu-berlin.de/en> |
| university | `established_year` | <https://www.itu.edu.tr> |
| university | `established_year` | <https://www.jku.at> |
| university | `established_year` | <https://www.jyu.fi/en> |
| university | `established_year` | <https://www.kaist.ac.kr/en/> |
| university | `established_year` | <https://www.kau.edu.sa> |
| university | `established_year` | <https://www.kaust.edu.sa> |
| university | `established_year` | <https://www.keio.ac.jp/en/> |
| university | `established_year` | <https://www.kfupm.edu.sa> |
| university | `established_year` | <https://www.kit.edu/english/> |
| university | `established_year` | <https://www.korea.edu/> |
| university | `established_year` | <https://www.ksu.edu.sa> |
| university | `established_year` | <https://www.ku.edu.tr> |
| university | `established_year` | <https://www.kyoto-u.ac.jp/en> |
| university | `established_year` | <https://www.kyushu-u.ac.jp/en/> |
| university | `established_year` | <https://www.lmu.de/en/> |
| university | `established_year` | <https://www.lut.fi/en> |
| university | `established_year` | <https://www.mcgill.ca/> |
| university | `established_year` | <https://www.metu.edu.tr> |
| university | `established_year` | <https://www.monash.edu.my/> |
| university | `established_year` | <https://www.monash.edu/> |
| university | `established_year` | <https://www.ntu.edu.sg> |
| university | `established_year` | <https://www.nus.edu.sg> |
| university | `established_year` | <https://www.osaka-u.ac.jp/en> |
| university | `established_year` | <https://www.oulu.fi/en> |
| university | `established_year` | <https://www.smu.edu.sg> |
| university | `established_year` | <https://www.sydney.edu.au/> |
| university | `established_year` | <https://www.tcd.ie/> |
| university | `established_year` | <https://www.tohoku.ac.jp/en/> |
| university | `established_year` | <https://www.tsukuba.ac.jp/en/> |
| university | `established_year` | <https://www.tu.berlin/en/> |
| university | `established_year` | <https://www.tugraz.at> |
| university | `established_year` | <https://www.tuni.fi/en> |
| university | `established_year` | <https://www.tuwien.at> |
| university | `established_year` | <https://www.u-tokyo.ac.jp/en/> |
| university | `established_year` | <https://www.ualberta.ca/> |
| university | `established_year` | <https://www.ub.edu> |
| university | `established_year` | <https://www.ubc.ca/> |
| university | `established_year` | <https://www.uc3m.es> |
| university | `established_year` | <https://www.ucc.ie/> |
| university | `established_year` | <https://www.ucd.ie/> |
| university | `established_year` | <https://www.ucm.es> |
| university | `established_year` | <https://www.uef.fi/en> |
| university | `established_year` | <https://www.uibk.ac.at> |
| university | `established_year` | <https://www.um.edu.my/> |
| university | `established_year` | <https://www.uni-freiburg.de/en> |
| university | `established_year` | <https://www.uni-heidelberg.de/en> |
| university | `established_year` | <https://www.uni-stuttgart.de/en/> |
| university | `established_year` | <https://www.unimelb.edu.au/> |
| university | `established_year` | <https://www.universityofgalway.ie/> |
| university | `established_year` | <https://www.univie.ac.at> |
| university | `established_year` | <https://www.unsw.edu.au/> |
| university | `established_year` | <https://www.upf.edu> |
| university | `established_year` | <https://www.upm.edu.my/> |
| university | `established_year` | <https://www.uq.edu.au/> |
| university | `established_year` | <https://www.utm.my/> |
| university | `established_year` | <https://www.utoronto.ca/> |
| university | `established_year` | <https://www.utu.fi/en> |

## UNCHECKABLE - cannot be reached from here

These say nothing about the data. A blocked page is a property of the
publisher's bot policy, not of our figures; they must be checked in a browser.

| Cited page | Why |
|---|---|
| <https://lacaixafoundation.org/en/doctoral-inphinit-fellowships> | HTTP 403 |
| <https://third-party-aggregator.example.com/uta> | ConnectError |
| <https://www.yonsei.ac.kr/en_sc/> | HTTP 404 (page gone) |

## Per-page summary

| Cited page | Confirmed | Not found | Fetch |
|---|---:|---:|---|
| <https://ee.um.edu.my/academic-staff> | 0 | 0 | RemoteProtocolError |
| <https://en.nagoya-u.ac.jp/> | 0 | 0 | HTTP 200 |
| <https://en.snu.ac.kr/> | 0 | 0 | HTTP 200 |
| <https://engine.um.edu.my/management> | 0 | 0 | RemoteProtocolError |
| <https://engine.um.edu.my/research-centre> | 0 | 0 | RemoteProtocolError |
| <https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters> | 1 | 2 | HTTP 200 |
| <https://lacaixafoundation.org/en/doctoral-inphinit-fellowships> | 0 | 0 | HTTP 403 |
| <https://oead.at/en/to-austria/grants-and-scholarships> | 2 | 1 | HTTP 200 |
| <https://postech.ac.kr/eng/> | 5 | 11 | HTTP 200 |
| <https://studyinaustria.at/> | 0 | 0 | HTTP 200 |
| <https://studyinsaudi.moe.gov.sa/> | 0 | 0 | ConnectError |
| <https://third-party-aggregator.example.com/uta> | 0 | 0 | ConnectError |
| <https://tu-dresden.de/> | 0 | 0 | HTTP 200 |
| <https://tu-dresden.de/bu/umwelt/die-fakultaet/professuren?set_language=en> | 0 | 0 | HTTP 200 |
| <https://tu-dresden.de/bu/umwelt/hydro/ihm/das-institut/beschaeftigte> | 0 | 0 | HTTP 200 |
| <https://tu-dresden.de/bu/umwelt/hydro/ihm/das-institut/professuren> | 0 | 0 | HTTP 200 |
| <https://tu-dresden.de/bu/umwelt/hydro/wald-wasser-dynamik/kernteam?set_language=en> | 0 | 0 | HTTP 200 |
| <https://uwaterloo.ca/> | 0 | 0 | HTTP 200 |
| <https://www.aalto.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.aalto.fi/en/international-students/scholarships-study-right-started-on-or-after-1-august-2025> | 2 | 1 | HTTP 200 |
| <https://www.aecid.es/en/scholarships-lectureships> | 1 | 2 | HTTP 200 |
| <https://www.bogazici.edu.tr> | 0 | 0 | HTTP 200 |
| <https://www.dcu.ie/> | 0 | 0 | HTTP 200 |
| <https://www.fundacioncarolina.es/formacion/formacion-y-becas-de-la-fundacion-carolina/> | 1 | 2 | HTTP 200 |
| <https://www.global.hokudai.ac.jp/> | 0 | 0 | HTTP 200 |
| <https://www.helsinki.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.helsinki.fi/en/admissions-and-education/apply-bachelors-and-masters-programmes> | 1 | 2 | HTTP 200 |
| <https://www.hu-berlin.de/en> | 0 | 0 | HTTP 200 |
| <https://www.itu.edu.tr> | 0 | 0 | HTTP 200 |
| <https://www.jku.at> | 0 | 0 | HTTP 200 |
| <https://www.jyu.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.kaist.ac.kr/en/> | 0 | 0 | HTTP 200 |
| <https://www.kau.edu.sa> | 0 | 0 | HTTP 200 |
| <https://www.kaust.edu.sa> | 0 | 0 | HTTP 200 |
| <https://www.kaust.edu.sa/en/study/admissions> | 0 | 3 | HTTP 200 |
| <https://www.keio.ac.jp/en/> | 0 | 0 | HTTP 200 |
| <https://www.kfupm.edu.sa> | 0 | 0 | HTTP 200 |
| <https://www.kit.edu/english/> | 0 | 0 | HTTP 200 |
| <https://www.korea.edu/> | 0 | 0 | HTTP 200 |
| <https://www.ksu.edu.sa> | 0 | 0 | HTTP 200 |
| <https://www.ku.edu.tr> | 0 | 0 | HTTP 403 |
| <https://www.kyoto-u.ac.jp/en> | 0 | 0 | HTTP 200 |
| <https://www.kyushu-u.ac.jp/en/> | 0 | 0 | HTTP 200 |
| <https://www.lmu.de/en/> | 0 | 0 | HTTP 200 |
| <https://www.lut.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.mcgill.ca/> | 0 | 0 | HTTP 200 |
| <https://www.metu.edu.tr> | 0 | 0 | HTTP 200 |
| <https://www.moe.gov.sg/> | 0 | 0 | HTTP 202 |
| <https://www.moe.gov.sg/financial-matters/awards-scholarships> | 0 | 3 | HTTP 202 |
| <https://www.monash.edu.my/> | 0 | 0 | HTTP 403 |
| <https://www.monash.edu/> | 0 | 0 | HTTP 403 |
| <https://www.ntu.edu.sg> | 0 | 0 | HTTP 200 |
| <https://www.nus.edu.sg> | 0 | 0 | HTTP 200 |
| <https://www.osaka-u.ac.jp/en> | 0 | 0 | HTTP 200 |
| <https://www.oulu.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.rwth-aachen.de/go/id/a/?lidx=1> | 4 | 10 | HTTP 200 |
| <https://www.smu.edu.sg> | 0 | 0 | HTTP 200 |
| <https://www.studyinfinland.fi/> | 0 | 0 | HTTP 200 |
| <https://www.studyinjapan.go.jp/en/planning/scholarship/> | 1 | 2 | HTTP 200 |
| <https://www.studyinturkiye.gov.tr/> | 0 | 0 | HTTP 200 |
| <https://www.sydney.edu.au/> | 0 | 0 | HTTP 200 |
| <https://www.tcd.ie/> | 0 | 0 | HTTP 200 |
| <https://www.tcd.ie/scss/people/academic-staff/> | 0 | 0 | HTTP 200 |
| <https://www.tohoku.ac.jp/en/> | 0 | 0 | HTTP 200 |
| <https://www.tsukuba.ac.jp/en/> | 0 | 0 | HTTP 200 |
| <https://www.tu.berlin/en/> | 0 | 0 | HTTP 200 |
| <https://www.tugraz.at> | 0 | 0 | HTTP 200 |
| <https://www.tum.de/en/> | 0 | 0 | HTTP 200 |
| <https://www.tum.de/en/studies/fees/tuition> | 2 | 1 | HTTP 200 |
| <https://www.tuni.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.turkiyeburslari.gov.tr/> | 0 | 0 | HTTP 200 |
| <https://www.tuwien.at> | 0 | 0 | HTTP 200 |
| <https://www.u-tokyo.ac.jp/en/> | 0 | 0 | HTTP 200 |
| <https://www.ualberta.ca/> | 0 | 0 | HTTP 200 |
| <https://www.ub.edu> | 0 | 0 | HTTP 403 |
| <https://www.ubc.ca/> | 0 | 0 | HTTP 200 |
| <https://www.uc3m.es> | 0 | 0 | HTTP 200 |
| <https://www.ucc.ie/> | 0 | 0 | HTTP 200 |
| <https://www.ucd.ie/> | 0 | 0 | HTTP 200 |
| <https://www.ucm.es> | 0 | 0 | HTTP 200 |
| <https://www.uef.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.uibk.ac.at> | 0 | 0 | HTTP 200 |
| <https://www.um.edu.my/> | 0 | 0 | HTTP 200 |
| <https://www.uni-freiburg.de/en> | 0 | 0 | HTTP 200 |
| <https://www.uni-heidelberg.de/en> | 0 | 0 | HTTP 200 |
| <https://www.uni-stuttgart.de/en/> | 0 | 0 | HTTP 200 |
| <https://www.unimelb.edu.au/> | 0 | 0 | HTTP 403 |
| <https://www.universidades.gob.es/> | 0 | 0 | ConnectError |
| <https://www.universityofgalway.ie/> | 0 | 0 | HTTP 200 |
| <https://www.universityofgalway.ie/about-us/news-and-events/expert-directory/engineering/> | 0 | 0 | HTTP 200 |
| <https://www.universityofgalway.ie/science-engineering/engineering/civilengineering/civil-engineering/people/> | 0 | 0 | HTTP 200 |
| <https://www.universityofgalway.ie/science-engineering/school-of-computer-science/people/academicstaff/> | 0 | 0 | HTTP 200 |
| <https://www.univie.ac.at> | 0 | 0 | HTTP 200 |
| <https://www.unsw.edu.au/> | 0 | 0 | HTTP 200 |
| <https://www.upf.edu> | 0 | 0 | HTTP 200 |
| <https://www.upm.edu.my/> | 0 | 0 | HTTP 200 |
| <https://www.uq.edu.au/> | 0 | 0 | HTTP 200 |
| <https://www.uta.edu/admissions> | 0 | 1 | HTTP 200 |
| <https://www.uta.edu/tuition> | 0 | 1 | HTTP 200 |
| <https://www.utm.my/> | 0 | 0 | HTTP 200 |
| <https://www.utoronto.ca/> | 0 | 0 | HTTP 200 |
| <https://www.utu.fi/en> | 0 | 0 | HTTP 200 |
| <https://www.waseda.jp/top/en> | 3 | 13 | HTTP 200 |
| <https://www.yonsei.ac.kr/en_sc/> | 0 | 0 | HTTP 404 (page gone) |

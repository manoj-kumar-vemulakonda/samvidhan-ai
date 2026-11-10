---
id: check-fir
title: Check an FIR
icon: 🚓
where: both
documents: required
output: report
inputs:
- date_of_offence | Date of the alleged offence (decides IPC or BNS) | date
- client_role | Your client is... (accused / complainant / victim) | text
- instructions | Anything else to look at? | textarea
---
## Purpose
Reads an FIR and explains the offences charged, which law applies (IPC or BNS by the date of the offence), whether each offence is bailable and cognizable, the client's rights, gaps in the FIR, and next steps.

## Steps
1. Read the FIR: police station, FIR number and date, informant, accused, date and place of the offence, delay in lodging it.
2. List every section invoked and check it is the right law for the date of the offence (IPC before 1 July 2024, BNS from then), giving the equivalent section.
3. For each offence: its ingredients, the punishment, and whether it is cognizable and bailable.
4. Check the FIR for gaps and inconsistencies: missing ingredients, vague allegations, delay, improbabilities.
5. Set out the client's rights and the immediate next steps (anticipatory or regular bail, notice of appearance, quashing, protest petition).

## Instructions
- Quote the FIR with page citations like [Doc 3, p. 1].
- Use the statute sections in the sources for the ingredients and punishment, and cite them.
- For BNS offences, take cognizable / bailable / which court tries it from the BNSS First Schedule in the sources, and cite it. For IPC offences (before 1 July 2024) the CrPC First Schedule is not in the library: give the classification from your own knowledge and mark it "(verify)".
- If the date of the offence is not given or shown, say which law applies on each assumption.
- End with a short table: Section | Offence | Punishment | Cognizable? | Bailable?

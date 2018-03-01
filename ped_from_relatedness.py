#!/usr/bin/env python3

import sys
import argparse
from parse_vcf import VcfReader, VcfHeader, VcfRecord 
from vase.ped_file import Family, Individual
from collections import defaultdict

par = {'hg38':   (('chrX', 10001, 2781479), ('chrX', 155701383, 156030895),
                 ('chrY', 10001, 2781479), ('chrY', 56887903, 57217415)),
       'GRCh38': (('X', 10001, 2781479), ('X', 155701383, 156030895),
                  ('Y', 10001, 2781479), ('Y', 56887903, 57217415)),
       'hg19':   (('chrX', 60001, 2699520), ('chrX', 154931044, 155260560),
                  ('chrY', 10001,   2649520), ('chrY', 59034050, 59363566)),
       'GRCh37': (('X', 60001, 2699520), ('X', 154931044, 155260560),
                  ('Y', 10001,   2649520), ('Y', 59034050, 59363566))}
non_par = {'hg38':   ('chrX', 2781479, 155701382), 
           'GRCh38': ('X', 2781479, 155701382), 
           'hg19':   ('chrX', 2699520, 154931043), 
           'GRCh37': ('X', 2699520, 154931043)}

gender_cutoff = 0.3 #min ratio of het to hom vars to consider a sample XX

def main(relatedness, vcf, ped=None, relatedness2=False, assembly='hg38', 
         min_gq=20, family_cutoff=None, first_degree_cutoff=None, 
         xx_cutoff=None, dup_cutoff=None, max_x_vars=None, pass_filters=False):
    global gender_cutoff
    cutoffs = dict()
    #cutoffs['par'] = 0.225 if relatedness2 else 0.35
    cutoffs['1st'] = 0.2 if relatedness2 else 0.35
    cutoffs['rel'] = 0.08 if relatedness2 else 0.125
    cutoffs['dup'] = 0.45 if relatedness2 else 0.8
    if family_cutoff:
        cutoffs['rel'] = family_cutoff
    if first_degree_cutoff:
        cutoffs['1st'] = first_degree_cutoff
    if dup_cutoff:
        cutoffs['dup'] = dup_cutoff
    if xx_cutoff:
        gender_cutoff = xx_cutoff
    scores = defaultdict(dict)
    with open(relatedness, 'rt') as infile:
        for line in infile:
            if line.startswith("INDV1"):
                continue 
            cols = line.rstrip().split()
            scores[cols[0]][cols[1]] = float(cols[-1])
    warn_of_duplicate_samples(scores, cutoffs)
    genders,x_het_ratio = infer_gender(vcf, assembly, scores, min_gq, 
                          max_x_vars=max_x_vars, pass_filters=pass_filters)
    if ped:
        check_ped(scores, genders, x_het_ratios, ped, cutoffs)
    else:
        construct_families(scores, genders, x_het_ratio, cutoffs)

def warn_of_duplicate_samples(scores, cutoffs):
    dups = set()
    for i1 in scores:
        for i2 in scores[i1]:
            if i1 == i2:
                continue
            if scores[i1][i2] >= cutoffs['dup']:
                dups.add(str.join(" + ", sorted((i1, i2))) + " ({})"
                         .format(scores[i1][i2]))
    if dups:
        sys.stderr.write("WARNING: Identified {:,} ".format(len(dups)) + 
                         "potential duplicate samples as follows:\n\t" + 
                         str.join("\n\t", sorted(dups)) + "\n")

def construct_families(scores, genders, x_het_ratios, cutoffs):
    fams = assign_fams(scores, cutoffs)
    print(str.join("\t", ("FAM", "INDV", "DAD", "MUM", "GENDER", "STATUS",
                          "PAT-REL", "MAT-REL", "PAR-REL", "X-HET-RATIO")))
    f = 0
    for indvs in fams.values():
        f += 1 
        nuclear_groups = get_nuclear_groups(indvs, scores, cutoffs)
        rows = parse_nuclear_groups(nuclear_groups, scores, genders, 
                                    x_het_ratios, cutoffs)
        for r in rows:
            print(str.join("\t", ["Fam_{}".format(f)] + r))

def infer_gender(f, assembly, scores, min_gq, max_x_vars=None, 
                 pass_filters=False):
    sys.stderr.write("Parsing VCF '{}' to infer sample genders.\n".format(f))
    vcf = VcfReader(f)
    check_samples_in_vcf(vcf, scores)
    vcf.set_region(non_par[assembly][0], non_par[assembly][1], 
                   non_par[assembly][2])
    het_counts = defaultdict(int)
    total_counts = defaultdict(int)#total variant (i.e. non 0/0) sites
    n = 0
    v = 0
    sys.stderr.write("Parsing variants in non-PAR coorinates ({}:{}-{})\n"
                     .format(non_par[assembly][0], non_par[assembly][1],
                             non_par[assembly][2]))
    for record in vcf.parser:
        n += 1
        if pass_filters and record.FILTER != 'PASS':
            continue
        if max_x_vars and v >= max_x_vars:
            break
        elif n % 1000 == 0:
            sys.stderr.write("\rRead {:,} variants on chrX {:,}".format(n,v) + 
                            " valid variants for inferring gender.")
        valid_gt = False
        gts = record.parsed_gts(fields=['GT', 'GQ'])
        for samp in gts['GT']:
            if gts['GQ'][samp] is None or gts['GQ'][samp] < min_gq:
                continue
            valid_gt = True
            alleles = set(gts['GT'][samp])
            if len(alleles) > 1:
                het_counts[samp] += 1
                total_counts[samp] += 1
            elif 0 not in alleles:
                total_counts[samp] += 1
        v += valid_gt
    if not total_counts:
        raise RuntimeError("No qualifying variants identified in non PAR " + 
                           "region {}:{}-{}.".format(non_par[assembly][0], 
                                                     non_par[assembly][1],
                                                     non_par[assembly][2]) + 
                           " Have you specified the correct assembly?")
    genders = dict()
    ratios = dict()
    for samp in total_counts:
        if total_counts[samp] < 1:
            genders[samp] = 0
            ratios[samp] = 0.0
        else:
            ratios[samp] = het_counts[samp]/total_counts[samp]
            if ratios[samp] > gender_cutoff: #female
                genders[samp] = 2
            else:
                genders[samp] = 1
    sys.stderr.write("\nFinished parsing variants\n")
    return (genders, ratios)

def check_samples_in_vcf(vcf, scores): 
    sys.stderr.write("Checking samples...\n")
    for indv1, other in scores.items():
        if indv1 not in vcf.header.samples:
            raise RuntimeError("ERROR: Could not find individual '{}' "
                               .format(indv1) + "from relatedness file in VCF")
        for indv2 in other:
            if indv2 not in vcf.header.samples:
                raise RuntimeError("ERROR: Could not find individual '{}' "
                                   .format(indv2) + "from relatedness file " + 
                                   "in VCF")
    sys.stderr.write("All samples from relatedness file found in VCF.\n")

def parse_nuclear_groups(groups, scores, genders, x_ratios, cutoffs):
    # everyone in each group should be first-degree relative of at least one
    # other person in the same group.
    # Should be able to infer parents from individuals that are not 1st-degree 
    # relatives of each other but share first-degree relatives
    unrel = defaultdict(list)
    children = defaultdict(dict)
    records = []
    for grp in groups:
        for i in range(0, len(grp)):
            for j in range(i+1, len(grp)):
                rel = get_score(grp[i], grp[j], scores)
                if rel < cutoffs['1st']:
                    unrel[grp[i]].append(grp[j])
        for par1 in unrel:
            for par2 in unrel[par1]:
                if same_gender(par1, par2, genders):
                    #require parents genders to differ or be unknown
                    continue
                for child in grp:
                    if child == par1 or child == par2:
                        continue
                    rel1 = get_score(par1, child, scores)
                    rel2 = get_score(par2, child, scores)
                    if rel1 < cutoffs['1st'] or rel2 < cutoffs['1st']:
                        continue
                    #if we know the gender of at least one parent assign accordingly
                    if genders[par1] == 1 or genders[par2] == 2:
                        dad = par1
                        mum = par2
                        pat_rel = rel1
                        mat_rel = rel2
                    elif genders[par1] == 2 or genders[par2] == 1:
                        dad = par2
                        mum = par1
                        pat_rel = rel2
                        mat_rel = rel1
                    else: #gender of both parents unknown - assign arbitrarily
                        dad = par1
                        mum = par2
                        pat_rel = rel1
                        mat_rel = rel2
                    if child in children:
                        children[child] = pick_parents(children[child], child, 
                                                       dad, mum, scores)
                    else:
                        children[child]['father'] = dad
                        children[child]['mother'] = mum
        for g in grp:
            row = [g]
            if g in children:
                row.append(children[g]['father'])
                row.append(children[g]['mother'])
            else:
                row.extend(['0', '0'])
            row.append(str(genders[g]))
            row.append('-9') #affectation status unknown
            if g in children:
                row.append("{:.3f}".format(get_score(children[g]['father'], g, 
                                                     scores)))
                row.append("{:.3f}".format(get_score(children[g]['mother'], g, 
                                                     scores)))
                row.append("{:.3f}".format(get_score(children[g]['father'], 
                                                     children[g]['mother'], 
                                                     scores)))
            else:
                row.extend(['NA', 'NA', 'NA'])
            row.append("{:.3f}".format(x_ratios[g]))
            records.append(row)
    return records
        
def pick_parents(par_dict, child, dad, mum, scores):
    # add the difference in scores in the new parent child relationships 
    # a postive score indicates new relationships are better and should be 
    # reassigned
    score_diffs = 0 
    if par_dict['father'] != dad:
        #child to father more related == better
        rel1 = get_score(child, par_dict['father'], scores) 
        rel2 = get_score(child, dad, scores)
        score_diffs += (rel2 - rel1)
    if par_dict['mother'] != mum:
        #child to mother more related == better
        rel1 = get_score(child, par_dict['mother'], scores) 
        rel2 = get_score(child, mum, scores)
        score_diffs += (rel2 - rel1)
    #father to mother more related == worse
    rel1 = get_score(par_dict['father'], par_dict['mother'], scores)
    rel2 = get_score(dad, mum, scores)
    score_diffs += (rel1 - rel2)
    if score_diffs > 0:
        #reassign
        par_dict['father'] = dad
        par_dict['mother'] = mum
    return par_dict

def same_gender(indv1, indv2, genders):
    if genders[indv1] == genders[indv2]:
        if genders[indv1] != 0:
            return True
    return False

def get_nuclear_groups(indvs, scores, cutoffs):
    nuclear_groups = []
    for i in range(0, len(indvs)):
        for j in range(i+1, len(indvs)):
            rel = get_score(indvs[i], indvs[j], scores)
            if rel > cutoffs['1st']:
                assigned = False
                if not nuclear_groups:
                    assigned = True
                    nuclear_groups.append([indvs[i], indvs[j]])
                else:
                    for nc in nuclear_groups:
                        if indvs[i] in nc and indvs[j] in nc:
                            assigned = True
                            break
                        elif indvs[i] in nc:
                            assigned = True
                            nc.append(indvs[j])
                        elif indvs[j] in nc:
                            assigned = True
                            nc.append(indvs[i])
                if not assigned:
                    nuclear_groups.append([indvs[i], indvs[j]])
    for indv in indvs: #ensure individuals not grouped are in their own group
        grouped = False
        for nc in nuclear_groups:
            if indv in nc:
                grouped = True
                break
        if not grouped:
            nuclear_groups.append([indv])
    return nuclear_groups

def assign_fams(scores, cutoffs):
    fams = defaultdict(list)
    n = 0
    indv_to_fam = dict()
    for indv1 in scores:
        for indv2 in scores[indv1]:
            if indv1 == indv2:
                continue
            if scores[indv1][indv2] >= cutoffs['rel']:
                assigned = False
                if indv1 in indv_to_fam:
                    i1 = indv1
                    i2 = indv2
                    assigned = True
                elif indv2 in indv_to_fam:
                    i1 = indv2
                    i2 = indv1
                    assigned = True
                if assigned:
                    if i2 in indv_to_fam: #already assigned - join families
                        f1 = indv_to_fam[i1]
                        f2 = indv_to_fam[i2]
                        if f1 != f2:
                            fams[f1].extend(fams[f2])
                            for i in fams[f2]:
                                indv_to_fam[i] = f1
                            del fams[f2]
                    else:
                        f = indv_to_fam[i1]
                        indv_to_fam[i2] = f
                        if i2 not in fams[f]:
                            fams[n].append(i2)
                else:
                    n += 1
                    indv_to_fam[indv1] = n
                    indv_to_fam[indv2] = n
                    fams[n].extend([indv1, indv2])
    return fams
    

def check_ped(scores, genders, x_ratios, pedfile, cutoffs):
    raise NotImplementedError

def get_score(indv1, indv2, scores):
    if indv1 in scores and indv2 in scores[indv1]: 
        return scores[indv1][indv2]
    elif indv2 in scores and indv1 in scores[indv2]:
        return scores[indv2][indv1]
    else:
        raise RuntimeError("Could not find relatedness score for {} vs {}"
                           .format(indv1, indv2))

def get_parser():
    '''Get ArgumentParser'''
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage='%(prog)s RELATEDNESS VCF [options]',
        description='Construct small pedigrees from relatedness data.',
    )

    parser.add_argument('relatedness', metavar='RELATEDNESS', 
                        help='''Input relatedness/relatedness2 file.''')
    parser.add_argument('vcf', metavar='VCF', 
                        help='''Input VCF file containing at a minimum, 
                                genotypes on the X chromosome.''')
    parser.add_argument('-p', '--ped', 
                        help='''PED file. If provided, relationships in this 
                                PED file will be checked and flagged if 
                                problematic rather than outputting the default 
                                output.''')
    parser.add_argument('-r2', '--relatedness2', action='store_true',
                        help='''Input is output of --relatedness2 command from
                                vcftools. Default is to assume input is from
                                the --relatedness option of vcftools. This 
                                adjusts the default relatedness cutoffs 
                                accordingly.''')
    parser.add_argument('-f', '--family_cutoff', type=float, 
                        help='''Custom relatedness value cutoff to consider two 
                                samples related. Depending on the type of data
                                used (e.g. WES vs WGS) you may need to tune
                                this threshold to find a sensible value. 
                                Default value is 0.125 or 0.08 if 
                                --relatedness2 flag is set.''')
    parser.add_argument('-1', '--first_degree_cutoff', type=float, 
                        help='''Custom relatedness value cutoff to consider two 
                                samples first degree relatives. Default value 
                                is 0.35 or 0.2 if --relatedness2 flag is 
                                set.''')
    parser.add_argument('-d', '--dup_cutoff', type=float, 
                        help='''Custom relatedness value cutoff to consider two 
                                samples as potential duplicates. Default value 
                                is 0.8 or 0.45 if --relatedness2 flag is 
                                set.''')
    parser.add_argument('-x', '--xx_cutoff', type=float, 
                        help='''Custom cutoff of for ratio het variants to 
                                total variants for assigning a sample as XX 
                                rather than XY. Default = 0.3.''')
    parser.add_argument('-a', '--assembly', default='hg38', 
                        help='''Assembly VCF input. Used for defining PAR 
                                region when determining genders. 
                                Default=hg38.''')
    parser.add_argument('-m', '--min_gq', type=int, default=20,
                        help='''Minimum genotype quality. Genotype calls with a
                                GQ below this value will be ignored. 
                                Default=20.''')
    parser.add_argument('-n', '--max_x_vars', type=int, 
                        help='''Maximum number of variants from the X 
                                chromosome to test when checking genders.
                                Default behaviour is to check all variants in 
                                the non-pseudoautosomal region. Use this option
                                if your VCF is large and checking X chromosome
                                variants takes a longer than desired.''')
    parser.add_argument('--pass_filters', action='store_true',
                        help='''Only use variants with 'PASS' in the filter
                                field for inferring gender.''')
            
    return parser

if __name__ == '__main__':
    parser = get_parser()
    args = parser.parse_args()
    main(**vars(args))

"""Read cached processed results from the authoritative XLSX without editing it.

Uses the Python standard library so CI does not require a spreadsheet package.
Workbook-level checks do not certify sensors, safety or raw time-series data.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from statistics import mean, stdev, correlation
import math
import posixpath
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def read_cached_workbook(path):
    sheets = {}
    with ZipFile(path) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        rels = {e.attrib['Id']: e.attrib['Target'] for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for s in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
            target = rels[s.attrib['{' + REL + '}id']]
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            cells = {}
            for c in ET.fromstring(z.read(target)).findall('.//s:sheetData/s:row/s:c', NS):
                kind = c.attrib.get('t')
                v = c.find('s:v', NS)
                if kind == 'inlineStr':
                    value = ''.join(n.text or '' for n in c.findall('.//s:t', NS))
                elif v is None or v.text is None:
                    value = None
                elif kind == 's':
                    value = strings[int(v.text)]
                elif kind in ('str', 'e'):
                    value = v.text
                else:
                    value = float(v.text)
                cells[c.attrib['r']] = value
            sheets[s.attrib['name']] = cells
    return sheets


def extract_metrics(path):
    w = read_cached_workbook(path)
    gas, sht, hx, run, joint = [w[n] for n in ['电子鼻_处理结果', 'SHT31_处理结果', 'HX711_标定结果', '整机稳定性', '联合采集汇总']]
    values = {
        'MOS-01': {'quantity': str(int(gas['S8']))},
        'MOS-02': {'min': round(min(gas[f'G{i}'] for i in range(5, 9)), 2), 'max': round(max(gas[f'G{i}'] for i in range(5, 9)), 2)},
        'MOS-03': {'min': min(gas[f'P{i}'] for i in range(5, 9)), 'max': max(gas[f'P{i}'] for i in range(5, 9))},
        'MOS-04': {'value': round(mean(gas[f'O{i}'] for i in range(5, 9)), 2)},
        'MOS-05': {'value': round(gas['S9'], 2)},
        'MOS-06': {'value': round(gas['S10'], 2)},
        'SHT-01': {'value': len({sht[f'A{i}'].split('-')[0] for i in range(5, 15)})},
        'SHT-02': {'value': sht['C5']},
        'SHT-03': {'value': round(sht['T5'], 2)},
        'SHT-04': {'value': round(sht['T6'], 3)},
        'SHT-05': {'value': round(sht['T7'], 3)},
        'HX-01': {'value': hx['A5']}, 'HX-02': {'value': hx['A14']},
        'HX-03': {'value': 10}, 'HX-04': {'value': 5},
        'HX-05': {'value': round(hx['P5'], 3)},
        'HX-06': {'value': round(hx['P6'], 3)},
        'HX-07': {'value': round(hx['P7'], 3)},
        'HX-08': {'value': round(hx['P8'], 8)},
        'RUN-01': {'value': run['C14']}, 'RUN-02': {'value': run['E14']},
        'RUN-03': {'value': run['F14']}, 'RUN-04': {'value': round(run['G14'], 2)},
        'RUN-05': {'value': run['I14']}, 'RUN-06': {'value': run['K14']},
        'TEA-01': {'value': sum(joint[f'C{i}'] for i in range(5, 15))},
        'TEA-02': {'value': sum(joint[f'M{i}'] for i in range(5, 15))},
        'TEA-03': {'value': sum(joint[f'N{i}'] for i in range(5, 15))},
        'TEA-04': {'value': round(sum(joint[f'N{i}'] for i in range(5, 15))/sum(joint[f'M{i}'] for i in range(5, 15))*100, 2)},
        'TEA-05': {'value': joint['I5']}, 'TEA-06': {'value': joint['J14']},
        'TEA-07': {'value': round(joint['I5']-joint['J14'], 3)},
        'TEA-08': {'value': round((joint['I5']-joint['J14'])/joint['I5']*100, 2)},
    }
    references = {
        'MOS-01': '电子鼻_处理结果!S8', 'MOS-02': '电子鼻_处理结果!G5:G8',
        'MOS-03': '电子鼻_处理结果!P5:P8', 'MOS-04': '电子鼻_处理结果!O5:O8 平均',
        'MOS-05': '电子鼻_处理结果!S9', 'MOS-06': '电子鼻_处理结果!S10',
        'SHT-01': 'SHT31_处理结果!A5:A14 去除测点后5组', 'SHT-02': 'SHT31_处理结果!C5:C14',
        'SHT-03': 'SHT31_处理结果!T5', 'SHT-04': 'SHT31_处理结果!T6', 'SHT-05': 'SHT31_处理结果!T7',
        'HX-01': 'HX711_标定结果!A5', 'HX-02': 'HX711_标定结果!A14',
        'HX-03': 'HX711_标定结果!A5:A14 10点', 'HX-04': 'HX711_标定结果!B5:F14 每点5次',
        'HX-05': 'HX711_标定结果!P5', 'HX-06': 'HX711_标定结果!P6',
        'HX-07': 'HX711_标定结果!P7', 'HX-08': 'HX711_标定结果!P8',
        'RUN-01': '整机稳定性!C14', 'RUN-02': '整机稳定性!E14', 'RUN-03': '整机稳定性!F14',
        'RUN-04': '整机稳定性!G14', 'RUN-05': '整机稳定性!I14', 'RUN-06': '整机稳定性!K14',
        'TEA-01': '联合采集汇总!C5:C14 求和', 'TEA-02': '联合采集汇总!M5:M14 求和',
        'TEA-03': '联合采集汇总!N5:N14 求和', 'TEA-04': '联合采集汇总!N5:N14总和/M5:M14总和',
        'TEA-05': '联合采集汇总!I5', 'TEA-06': '联合采集汇总!J14',
        'TEA-07': '联合采集汇总!I5-J14', 'TEA-08': '联合采集汇总!(I5-J14)/I5*100',
    }
    checks = 0
    def close(a, b):
        nonlocal checks
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f'workbook cached result mismatch: {a} vs {b}')
        checks += 1
    for i in range(5, 9):
        repeats = [gas[f'{c}{i}'] for c in 'HIJKL']
        close(gas[f'F{i}'], (gas[f'E{i}']-gas[f'C{i}'])/gas[f'C{i}'])
        close(gas[f'G{i}'], abs(gas[f'F{i}'])*100)
        close(gas[f'M{i}'], mean(repeats)); close(gas[f'N{i}'], stdev(repeats))
        close(gas[f'O{i}'], stdev(repeats)/abs(mean(repeats))*100)
    for i in range(5, 15):
        close(sht[f'G{i}'], sht[f'F{i}']/sht[f'E{i}']*100)
        close(sht[f'M{i}'], abs(sht[f'H{i}']-sht[f'L{i}']))
        close(sht[f'Q{i}'], abs(sht[f'N{i}']-sht[f'P{i}']))
        repeated = [hx[f'{c}{i}'] for c in 'BCDEF']
        close(hx[f'G{i}'], mean(repeated)); close(hx[f'J{i}'], stdev(repeated))
        close(hx[f'I{i}'], abs(mean(repeated)-hx[f'A{i}'])/hx[f'A{i}']*100)
        close(run[f'G{i}'], run[f'F{i}']/run[f'E{i}']*100)
        close(joint[f'K{i}'], joint[f'J{i}']-joint[f'I{i}'])
        close(joint[f'L{i}'], (joint[f'J{i}']-joint[f'I{i}'])/joint[f'I{i}']*100)
        close(joint[f'O{i}'], joint[f'N{i}']/joint[f'M{i}']*100)
    close(hx['P8'], correlation([hx[f'A{i}'] for i in range(5,15)], [hx[f'G{i}'] for i in range(5,15)])**2)
    flags = [w['申报材料摘要'].get(f'E{i}') for i in range(4,15)]
    discrepancies = [{'test_id': run[f'A{i}'], 'count_difference': int(run[f'E{i}']-run[f'F{i}']),
                      'reported_packet_loss': run[f'H{i}']} for i in range(5,15)
                     if run[f'E{i}']-run[f'F{i}'] != run[f'H{i}']]
    return {'values': values, 'references': references, 'sha256': sha256(Path(path).read_bytes()).hexdigest(),
            'cached_calculations_checked': checks, 'submission_flags': flags,
            'packet_count_discrepancies': discrepancies}

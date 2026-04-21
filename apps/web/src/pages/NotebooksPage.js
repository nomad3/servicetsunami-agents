import { useState } from 'react';
import {
  Badge,
  Button,
  Col,
  Form,
  InputGroup,
  Modal,
  Row,
  Table
} from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import {
  FaBalanceScale,
  FaChartLine,
  FaExchangeAlt,
  FaFileExport,
  FaFileInvoiceDollar,
  FaFilePdf,
  FaMoneyBillWave,
  FaSearch,
  FaSearchDollar,
  FaTachometerAlt
} from 'react-icons/fa';
import Layout from '../components/Layout';
import './NotebooksPage.css';

const CATEGORY_COLORS = {
  Income: 'success',
  Balance: 'info',
  'Cash Flow': 'primary',
  Comparison: 'warning',
  Diligence: 'danger',
  KPIs: 'secondary',
};

const REPORT_TEMPLATES = [
  {
    id: 1,
    name: 'P&L Statement',
    category: 'Income',
    icon: FaChartLine,
    description: 'Revenue, expenses, and net income by business unit',
    frequency: 'Monthly',
    entities: 'All Units',
    status: 'Ready',
    lastGenerated: '2026-02-11',
    data: {
      columns: ['Metric', 'Unit A', 'Unit B', 'Unit C', 'Total'],
      rows: [
        ['Revenue', '$2,450,000', '$1,870,000', '$3,210,000', '$7,530,000'],
        ['COGS', '$980,000', '$748,000', '$1,284,000', '$3,012,000'],
        ['Gross Profit', '$1,470,000', '$1,122,000', '$1,926,000', '$4,518,000'],
        ['OpEx', '$735,000', '$561,000', '$963,000', '$2,259,000'],
        ['EBITDA', '$882,000', '$673,200', '$1,155,600', '$2,710,800'],
        ['Net Income', '$588,000', '$448,800', '$770,400', '$1,807,200'],
      ],
    },
  },
  {
    id: 2,
    name: 'Consolidated Balance Sheet',
    category: 'Balance',
    icon: FaBalanceScale,
    description: 'Assets, liabilities, and equity across the organization',
    frequency: 'Quarterly',
    entities: 'Consolidated',
    status: 'Ready',
    lastGenerated: '2026-01-31',
    data: {
      columns: ['Metric', 'Unit A', 'Unit B', 'Unit C', 'Total'],
      rows: [
        ['Cash & Equivalents', '$1,200,000', '$890,000', '$1,540,000', '$3,630,000'],
        ['Receivables', '$480,000', '$356,000', '$616,000', '$1,452,000'],
        ['Total Assets', '$4,800,000', '$3,560,000', '$6,160,000', '$14,520,000'],
        ['Payables', '$360,000', '$267,000', '$462,000', '$1,089,000'],
        ['Debt', '$1,440,000', '$1,068,000', '$1,848,000', '$4,356,000'],
        ['Equity', '$3,000,000', '$2,225,000', '$3,850,000', '$9,075,000'],
      ],
    },
  },
  {
    id: 3,
    name: 'Cash Flow Analysis',
    category: 'Cash Flow',
    icon: FaMoneyBillWave,
    description: 'Operating, investing, and financing cash flows',
    frequency: 'Monthly',
    entities: 'All Units',
    status: 'Ready',
    lastGenerated: '2026-02-10',
    data: {
      columns: ['Metric', 'Unit A', 'Unit B', 'Unit C', 'Total'],
      rows: [
        ['Operating', '$720,000', '$534,000', '$924,000', '$2,178,000'],
        ['Investing', '-$240,000', '-$178,000', '-$308,000', '-$726,000'],
        ['Financing', '-$180,000', '-$133,500', '-$231,000', '-$544,500'],
        ['Net Change', '$300,000', '$222,500', '$385,000', '$907,500'],
      ],
    },
  },
  {
    id: 4,
    name: 'Business Unit Comparison',
    category: 'Comparison',
    icon: FaExchangeAlt,
    description: 'Side-by-side financial performance across business units',
    frequency: 'Monthly',
    entities: 'All Units',
    status: 'Scheduled',
    lastGenerated: '2026-02-09',
    data: {
      columns: ['Metric', 'Unit A', 'Unit B', 'Unit C', 'Average'],
      rows: [
        ['Revenue', '$2,450,000', '$1,870,000', '$3,210,000', '$2,510,000'],
        ['Growth %', '12.4%', '8.7%', '15.2%', '12.1%'],
        ['Margin', '24.0%', '24.0%', '24.0%', '24.0%'],
        ['Headcount', '45', '32', '58', '45'],
      ],
    },
  },
  {
    id: 5,
    name: 'Business Health Assessment',
    category: 'Diligence',
    icon: FaSearchDollar,
    description: 'Financial health scores and risk indicators',
    frequency: 'On-demand',
    entities: 'Selected',
    status: 'Ready',
    lastGenerated: '2026-02-05',
    data: {
      columns: ['Metric', 'Unit A', 'Unit B', 'Unit C', 'Benchmark'],
      rows: [
        ['Revenue Trend', 'Growing', 'Stable', 'Growing', '—'],
        ['Debt/Equity', '0.48', '0.48', '0.48', '< 0.60'],
        ['Working Capital', '$1,320,000', '$979,000', '$1,694,000', '> $500K'],
        ['Risk Score', 'Low', 'Medium', 'Low', '—'],
      ],
    },
  },
  {
    id: 6,
    name: 'KPI Dashboard',
    category: 'KPIs',
    icon: FaTachometerAlt,
    description: 'Revenue growth, EBITDA margins, headcount trends',
    frequency: 'Weekly',
    entities: 'All Units',
    status: 'Generating',
    lastGenerated: '2026-02-11',
    data: {
      columns: ['KPI', 'This Week', 'Last Week', 'Change'],
      rows: [
        ['Revenue', '$1,882,500', '$1,810,000', '+4.0%'],
        ['EBITDA Margin', '36.0%', '35.2%', '+0.8pp'],
        ['Customer Count', '1,247', '1,218', '+2.4%'],
        ['Headcount', '135', '132', '+3'],
        ['MRR', '$627,500', '$603,333', '+4.0%'],
      ],
    },
  },
];

const STATUS_COLORS = {
  Ready: 'success',
  Generating: 'warning',
  Scheduled: 'info',
};

const NotebooksPage = () => {
  const { t } = useTranslation('notebooks');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedReport, setSelectedReport] = useState(null);

  const filteredReports = REPORT_TEMPLATES.filter(
    (r) =>
      r.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      r.category.toLowerCase().includes(searchTerm.toLowerCase()) ||
      r.description.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const scheduledCount = REPORT_TEMPLATES.filter(
    (r) => r.frequency !== 'On-demand'
  ).length;

  return (
    <Layout>
      <div className="notebooks-page">
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">{t('title')}</h1>
            <p className="ap-page-subtitle">{t('subtitle')}</p>
          </div>
          <div className="ap-page-actions">
            <span className="ap-badge-outline">{t('enterpriseSuite')}</span>
          </div>
        </header>

        <Row className="g-4 mb-4">
          <Col md={3}>
            <article className="ap-card h-100">
              <div className="ap-card-body">
                <div className="notebooks-stat-label">{t('stats.totalReports')}</div>
                <div className="notebooks-stat-value">{REPORT_TEMPLATES.length}</div>
                <div className="notebooks-stat-hint">{t('stats.financialTemplates')}</div>
              </div>
            </article>
          </Col>
          <Col md={3}>
            <article className="ap-card h-100">
              <div className="ap-card-body">
                <div className="notebooks-stat-label">{t('stats.coverage')}</div>
                <div className="notebooks-stat-value">All</div>
                <div className="notebooks-stat-hint">{t('stats.allUnits')}</div>
              </div>
            </article>
          </Col>
          <Col md={3}>
            <article className="ap-card h-100">
              <div className="ap-card-body">
                <div className="notebooks-stat-label">{t('stats.automated')}</div>
                <div className="notebooks-stat-value">{scheduledCount}</div>
                <div className="notebooks-stat-hint">{t('stats.ofScheduled', { total: REPORT_TEMPLATES.length })}</div>
              </div>
            </article>
          </Col>
          <Col md={3}>
            <article className="ap-card h-100">
              <div className="ap-card-body">
                <div className="notebooks-stat-label">{t('stats.lastUpdated')}</div>
                <div className="notebooks-stat-value">{t('stats.today')}</div>
                <div className="notebooks-stat-hint">{t('stats.allCurrent')}</div>
              </div>
            </article>
          </Col>
        </Row>

        <div className="ap-search-wrap mb-4" style={{ maxWidth: 'none' }}>
          <FaSearch size={14} />
          <input
            type="text"
            className="ap-search-input"
            placeholder={t('searchPlaceholder')}
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>

        <article className="ap-card">
          <Table hover responsive className="reports-table mb-0 ap-table">
            <thead>
              <tr>
                <th>{t('table.reportName')}</th>
                <th>{t('table.category')}</th>
                <th>{t('table.scope')}</th>
                <th>{t('table.frequency')}</th>
                <th>{t('table.status')}</th>
              </tr>
            </thead>
            <tbody>
              {filteredReports.map((report) => {
                const IconComponent = report.icon;
                return (
                  <tr
                    key={report.id}
                    className="report-row"
                    onClick={() => setSelectedReport(report)}
                  >
                    <td>
                      <div className="d-flex align-items-center gap-2">
                        <IconComponent size={16} aria-hidden="true" />
                        <div>
                          <strong>{report.name}</strong>
                          <div className="text-muted small">{report.description}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <Badge
                        bg={CATEGORY_COLORS[report.category]}
                        className={`bg-opacity-25 text-${CATEGORY_COLORS[report.category]} border border-${CATEGORY_COLORS[report.category]}`}
                      >
                        {report.category}
                      </Badge>
                    </td>
                    <td className="text-muted">{report.entities}</td>
                    <td className="text-muted">{report.frequency}</td>
                    <td>
                      <Badge
                        bg={STATUS_COLORS[report.status]}
                        className={`bg-opacity-25 text-${STATUS_COLORS[report.status]} border border-${STATUS_COLORS[report.status]}`}
                      >
                        {report.status}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
              {filteredReports.length === 0 && (
                <tr>
                  <td colSpan={5} className="text-center text-muted py-4">
                    {t('noResults')}
                  </td>
                </tr>
              )}
            </tbody>
          </Table>
        </article>

        <Modal
          show={!!selectedReport}
          onHide={() => setSelectedReport(null)}
          size="lg"
          centered
          className="report-modal"
        >
          {selectedReport && (
            <>
              <Modal.Header closeButton>
                <Modal.Title className="d-flex align-items-center gap-3">
                  <selectedReport.icon size={20} aria-hidden="true" />
                  <div>
                    <div>{selectedReport.name}</div>
                    <div className="d-flex align-items-center gap-2 mt-1">
                      <Badge
                        bg={CATEGORY_COLORS[selectedReport.category]}
                        className={`bg-opacity-25 text-${CATEGORY_COLORS[selectedReport.category]} border border-${CATEGORY_COLORS[selectedReport.category]}`}
                      >
                        {selectedReport.category}
                      </Badge>
                      <span className="text-muted small">
                        {selectedReport.frequency} &middot; {t('modal.lastGenerated', { date: selectedReport.lastGenerated })}
                      </span>
                    </div>
                  </div>
                </Modal.Title>
              </Modal.Header>
              <Modal.Body>
                <p className="text-muted mb-3">{selectedReport.description}</p>
                <div className="table-responsive">
                  <Table className="report-data-table ap-table mb-0">
                    <thead>
                      <tr>
                        {selectedReport.data.columns.map((col) => (
                          <th key={col}>{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {selectedReport.data.rows.map((row, idx) => (
                        <tr key={idx}>
                          {row.map((cell, cellIdx) => (
                            <td key={cellIdx} className={cellIdx === 0 ? 'fw-semibold' : ''}>
                              {cell}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </Modal.Body>
              <Modal.Footer>
                <button type="button" className="ap-btn-secondary" onClick={() => setSelectedReport(null)}>
                  <FaFileExport size={12} />
                  {t('modal.exportCsv')}
                </button>
                <button type="button" className="ap-btn-primary">
                  <FaFilePdf size={12} />
                  {t('modal.exportPdf')}
                </button>
              </Modal.Footer>
            </>
          )}
        </Modal>
      </div>
    </Layout>
  );
};

export default NotebooksPage;

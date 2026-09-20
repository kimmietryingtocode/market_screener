#include "backtest.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <numeric>
#include <set>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <utility>

namespace backtest {
namespace {

constexpr int kOneMonth = 21;
constexpr int kThreeMonths = 63;
constexpr int kSixMonths = 126;
constexpr double kTradingDaysPerYear = 252.0;
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

bool finite(double value) { return std::isfinite(value); }

std::string trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return "";
    }
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::vector<std::string> parse_csv_line(const std::string& line) {
    std::vector<std::string> fields;
    std::string field;
    bool quoted = false;
    for (std::size_t index = 0; index < line.size(); ++index) {
        const char character = line[index];
        if (character == '"') {
            if (quoted && index + 1 < line.size() && line[index + 1] == '"') {
                field.push_back('"');
                ++index;
            } else {
                quoted = !quoted;
            }
        } else if (character == ',' && !quoted) {
            fields.push_back(trim(field));
            field.clear();
        } else {
            field.push_back(character);
        }
    }
    if (quoted) {
        throw std::runtime_error("unterminated quoted CSV field");
    }
    fields.push_back(trim(field));
    return fields;
}

std::string csv_escape(const std::string& value) {
    if (value.find_first_of(",\"\n\r") == std::string::npos) {
        return value;
    }
    std::string escaped{"\""};
    for (const char character : value) {
        if (character == '"') {
            escaped += "\"\"";
        } else {
            escaped.push_back(character);
        }
    }
    escaped.push_back('"');
    return escaped;
}

std::map<std::string, std::size_t> header_map(const std::vector<std::string>& header) {
    std::map<std::string, std::size_t> result;
    for (std::size_t index = 0; index < header.size(); ++index) {
        result[header[index]] = index;
    }
    return result;
}

std::size_t require_column(
    const std::map<std::string, std::size_t>& columns,
    const std::string& name,
    const std::string& path) {
    const auto found = columns.find(name);
    if (found == columns.end()) {
        throw std::runtime_error(path + " is missing required column " + name);
    }
    return found->second;
}

std::string industry_key(const std::string& sector, const std::string& industry) {
    return sector + "\x1f" + industry;
}

std::optional<double> trailing_return(
    const std::vector<double>& prices, std::size_t end, int days) {
    if (end < static_cast<std::size_t>(days)) {
        return std::nullopt;
    }
    const double current = prices[end];
    const double previous = prices[end - static_cast<std::size_t>(days)];
    if (!finite(current) || !finite(previous) || previous <= 0.0) {
        return std::nullopt;
    }
    return current / previous - 1.0;
}

std::optional<double> annualized_volatility(
    const std::vector<double>& prices, std::size_t end, int days) {
    if (end < static_cast<std::size_t>(days)) {
        return std::nullopt;
    }
    std::vector<double> returns;
    returns.reserve(static_cast<std::size_t>(days));
    for (std::size_t index = end - static_cast<std::size_t>(days) + 1;
         index <= end;
         ++index) {
        if (!finite(prices[index]) || !finite(prices[index - 1]) || prices[index - 1] <= 0.0) {
            return std::nullopt;
        }
        returns.push_back(prices[index] / prices[index - 1] - 1.0);
    }
    if (returns.size() < 2) {
        return std::nullopt;
    }
    const double mean =
        std::accumulate(returns.begin(), returns.end(), 0.0) / returns.size();
    double squared = 0.0;
    for (const double value : returns) {
        squared += (value - mean) * (value - mean);
    }
    return std::sqrt(squared / (returns.size() - 1)) * std::sqrt(kTradingDaysPerYear);
}

std::optional<double> recent_drawdown(
    const std::vector<double>& prices, std::size_t end, int days) {
    if (end + 1 < static_cast<std::size_t>(days)) {
        return std::nullopt;
    }
    double peak = -std::numeric_limits<double>::infinity();
    double drawdown = 0.0;
    const std::size_t start = end + 1 - static_cast<std::size_t>(days);
    for (std::size_t index = start; index <= end; ++index) {
        if (!finite(prices[index]) || prices[index] <= 0.0) {
            return std::nullopt;
        }
        peak = std::max(peak, prices[index]);
        drawdown = std::min(drawdown, prices[index] / peak - 1.0);
    }
    return drawdown;
}

std::optional<MomentumMetrics> momentum_metrics(
    const std::vector<double>& prices,
    const std::vector<double>& benchmark,
    std::size_t end) {
    const auto one_month = trailing_return(prices, end, kOneMonth);
    const auto three_months = trailing_return(prices, end, kThreeMonths);
    const auto six_months = trailing_return(prices, end, kSixMonths);
    const auto benchmark_three_months = trailing_return(benchmark, end, kThreeMonths);
    const auto volatility = annualized_volatility(prices, end, kThreeMonths);
    const auto drawdown = recent_drawdown(prices, end, kThreeMonths);
    if (!one_month || !three_months || !six_months || !benchmark_three_months ||
        !volatility || !drawdown) {
        return std::nullopt;
    }
    return MomentumMetrics{
        *one_month,
        *three_months,
        *six_months,
        *three_months - *benchmark_three_months,
        *volatility,
        *drawdown,
    };
}

bool above_moving_average(
    const std::vector<double>& prices, std::size_t end, int days) {
    if (days <= 0) {
        return true;
    }
    if (end + 1 < static_cast<std::size_t>(days) || !finite(prices[end])) {
        return false;
    }
    double total = 0.0;
    for (std::size_t index = end + 1 - static_cast<std::size_t>(days);
         index <= end;
         ++index) {
        if (!finite(prices[index])) {
            return false;
        }
        total += prices[index];
    }
    return prices[end] >= total / static_cast<double>(days);
}

struct ScoredAsset {
    std::string key;
    MomentumMetrics metrics;
    double score{};
};

std::vector<double> percentile_ranks(const std::vector<double>& values) {
    std::vector<std::size_t> order(values.size());
    std::iota(order.begin(), order.end(), 0);
    std::stable_sort(order.begin(), order.end(), [&](std::size_t lhs, std::size_t rhs) {
        return values[lhs] < values[rhs];
    });

    std::vector<double> ranks(values.size());
    std::size_t start = 0;
    while (start < order.size()) {
        std::size_t end = start;
        while (end + 1 < order.size() &&
               std::abs(values[order[end + 1]] - values[order[start]]) < 1e-12) {
            ++end;
        }
        const double average_rank =
            (static_cast<double>(start + 1) + static_cast<double>(end + 1)) / 2.0;
        for (std::size_t position = start; position <= end; ++position) {
            ranks[order[position]] = average_rank / static_cast<double>(values.size());
        }
        start = end + 1;
    }
    return ranks;
}

std::vector<ScoredAsset> score_assets(
    const std::vector<std::pair<std::string, MomentumMetrics>>& assets) {
    if (assets.empty()) {
        return {};
    }
    std::vector<double> relative_strength;
    std::vector<double> six_months;
    std::vector<double> one_month;
    std::vector<double> inverse_volatility;
    for (const auto& [key, metrics] : assets) {
        (void)key;
        relative_strength.push_back(metrics.relative_strength_3m);
        six_months.push_back(metrics.return_6m);
        one_month.push_back(metrics.return_1m);
        inverse_volatility.push_back(-metrics.volatility_3m);
    }
    const auto rs_rank = percentile_ranks(relative_strength);
    const auto six_rank = percentile_ranks(six_months);
    const auto one_rank = percentile_ranks(one_month);
    const auto volatility_rank = percentile_ranks(inverse_volatility);

    std::vector<ScoredAsset> scored;
    for (std::size_t index = 0; index < assets.size(); ++index) {
        scored.push_back(ScoredAsset{
            assets[index].first,
            assets[index].second,
            100.0 * (0.40 * rs_rank[index] + 0.30 * six_rank[index] +
                     0.20 * one_rank[index] + 0.10 * volatility_rank[index]),
        });
    }
    std::sort(scored.begin(), scored.end(), [](const ScoredAsset& lhs, const ScoredAsset& rhs) {
        if (std::abs(lhs.score - rhs.score) > 1e-12) {
            return lhs.score > rhs.score;
        }
        return lhs.key < rhs.key;
    });
    return scored;
}

std::vector<double> build_equal_weight_index(
    const std::vector<std::string>& tickers,
    const PriceTable& prices) {
    std::vector<double> index(prices.dates.size(), kNaN);
    if (tickers.empty() || prices.dates.empty()) {
        return index;
    }

    std::size_t first = prices.dates.size();
    for (std::size_t date_index = 0; date_index < prices.dates.size(); ++date_index) {
        for (const auto& ticker : tickers) {
            if (finite(prices.series(ticker)[date_index])) {
                first = date_index;
                break;
            }
        }
        if (first != prices.dates.size()) {
            break;
        }
    }
    if (first == prices.dates.size()) {
        return index;
    }
    index[first] = 100.0;
    for (std::size_t date_index = first + 1; date_index < prices.dates.size(); ++date_index) {
        std::vector<double> returns;
        for (const auto& ticker : tickers) {
            const auto& series = prices.series(ticker);
            if (finite(series[date_index]) && finite(series[date_index - 1]) &&
                series[date_index - 1] > 0.0) {
                returns.push_back(series[date_index] / series[date_index - 1] - 1.0);
            }
        }
        if (returns.empty()) {
            index[date_index] = index[date_index - 1];
        } else {
            const double average =
                std::accumulate(returns.begin(), returns.end(), 0.0) / returns.size();
            index[date_index] = index[date_index - 1] * (1.0 + average);
        }
    }
    return index;
}

int month_number(const Date& date) { return date.year * 12 + date.month - 1; }

double portfolio_value(
    double cash,
    const std::map<std::string, double>& shares,
    const PriceTable& prices,
    std::size_t date_index) {
    double value = cash;
    for (const auto& [ticker, quantity] : shares) {
        const double price = prices.series(ticker)[date_index];
        if (!finite(price)) {
            throw std::runtime_error("missing price for held security " + ticker);
        }
        value += quantity * price;
    }
    return value;
}

double sample_standard_deviation(const std::vector<double>& values) {
    if (values.size() < 2) {
        return 0.0;
    }
    const double mean = std::accumulate(values.begin(), values.end(), 0.0) / values.size();
    double squared = 0.0;
    for (const double value : values) {
        squared += (value - mean) * (value - mean);
    }
    return std::sqrt(squared / (values.size() - 1));
}

Performance calculate_performance(
    const std::vector<Date>& dates,
    const std::vector<double>& values) {
    if (dates.size() != values.size() || dates.size() < 2 || values.front() <= 0.0) {
        throw std::runtime_error("insufficient observations for performance statistics");
    }
    Performance result;
    result.ending_value = values.back();
    result.total_return = values.back() / values.front() - 1.0;
    const double years =
        static_cast<double>(dates.back().serial() - dates.front().serial()) / 365.25;
    result.cagr = years > 0.0 && values.back() > 0.0
                      ? std::pow(values.back() / values.front(), 1.0 / years) - 1.0
                      : 0.0;

    std::vector<double> daily_returns;
    daily_returns.reserve(values.size() - 1);
    double peak = values.front();
    for (std::size_t index = 1; index < values.size(); ++index) {
        daily_returns.push_back(values[index] / values[index - 1] - 1.0);
        peak = std::max(peak, values[index]);
        result.maximum_drawdown =
            std::min(result.maximum_drawdown, values[index] / peak - 1.0);
    }
    const double daily_mean =
        std::accumulate(daily_returns.begin(), daily_returns.end(), 0.0) /
        daily_returns.size();
    const double daily_volatility = sample_standard_deviation(daily_returns);
    result.annualized_volatility = daily_volatility * std::sqrt(kTradingDaysPerYear);
    result.sharpe_ratio = daily_volatility > 0.0
                              ? daily_mean / daily_volatility * std::sqrt(kTradingDaysPerYear)
                              : 0.0;

    double downside_square_sum = 0.0;
    for (const double value : daily_returns) {
        const double downside = std::min(0.0, value);
        downside_square_sum += downside * downside;
    }
    const double downside_deviation =
        std::sqrt(downside_square_sum / daily_returns.size());
    result.sortino_ratio = downside_deviation > 0.0
                               ? daily_mean / downside_deviation * std::sqrt(kTradingDaysPerYear)
                               : 0.0;
    return result;
}

}  // namespace

Date Date::parse(const std::string& value) {
    if (value.size() != 10 || value[4] != '-' || value[7] != '-') {
        throw std::invalid_argument("invalid date: " + value);
    }
    Date result{std::stoi(value.substr(0, 4)), std::stoi(value.substr(5, 2)),
                std::stoi(value.substr(8, 2))};
    if (result.month < 1 || result.month > 12) {
        throw std::invalid_argument("invalid date: " + value);
    }
    const bool leap = result.year % 4 == 0 && (result.year % 100 != 0 || result.year % 400 == 0);
    const int month_days[] = {0, 31, leap ? 29 : 28, 31, 30, 31, 30,
                              31, 31, 30, 31, 30, 31};
    if (result.day < 1 || result.day > month_days[result.month]) {
        throw std::invalid_argument("invalid date: " + value);
    }
    return result;
}

std::string Date::str() const {
    std::ostringstream stream;
    stream << std::setfill('0') << std::setw(4) << year << '-' << std::setw(2) << month << '-'
           << std::setw(2) << day;
    return stream.str();
}

int Date::serial() const {
    int adjusted_year = year;
    const int adjusted_month = month;
    adjusted_year -= adjusted_month <= 2;
    const int era = (adjusted_year >= 0 ? adjusted_year : adjusted_year - 399) / 400;
    const unsigned year_of_era = static_cast<unsigned>(adjusted_year - era * 400);
    const unsigned day_of_year =
        static_cast<unsigned>(
            (153 * (adjusted_month + (adjusted_month > 2 ? -3 : 9)) + 2) / 5 + day - 1);
    const unsigned day_of_era =
        year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    return era * 146097 + static_cast<int>(day_of_era);
}

bool operator<(const Date& lhs, const Date& rhs) {
    return std::tie(lhs.year, lhs.month, lhs.day) < std::tie(rhs.year, rhs.month, rhs.day);
}

bool operator==(const Date& lhs, const Date& rhs) {
    return std::tie(lhs.year, lhs.month, lhs.day) == std::tie(rhs.year, rhs.month, rhs.day);
}

PriceTable PriceTable::load_csv(const std::string& path, const std::string& benchmark) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open price file: " + path);
    }
    std::string line;
    if (!std::getline(input, line)) {
        throw std::runtime_error("price file is empty: " + path);
    }
    const auto columns = header_map(parse_csv_line(line));
    const auto date_column = require_column(columns, "date", path);
    const auto ticker_column = require_column(columns, "ticker", path);
    const auto price_column = require_column(columns, "adjusted_close", path);

    std::map<std::string, std::map<Date, double>> raw;
    std::size_t row_number = 1;
    while (std::getline(input, line)) {
        ++row_number;
        if (trim(line).empty()) {
            continue;
        }
        const auto fields = parse_csv_line(line);
        const std::size_t required = std::max({date_column, ticker_column, price_column});
        if (fields.size() <= required) {
            throw std::runtime_error(path + ": short row " + std::to_string(row_number));
        }
        const Date date = Date::parse(fields[date_column]);
        const std::string ticker = fields[ticker_column];
        const double price = std::stod(fields[price_column]);
        if (ticker.empty() || !finite(price) || price <= 0.0) {
            throw std::runtime_error(path + ": invalid price row " + std::to_string(row_number));
        }
        if (!raw[ticker].emplace(date, price).second) {
            throw std::runtime_error(path + ": duplicate date/ticker row " +
                                     std::to_string(row_number));
        }
    }
    const auto benchmark_found = raw.find(benchmark);
    if (benchmark_found == raw.end()) {
        throw std::runtime_error("price file does not contain benchmark " + benchmark);
    }

    PriceTable result;
    for (const auto& [date, price] : benchmark_found->second) {
        (void)price;
        result.dates.push_back(date);
    }
    if (result.dates.size() < static_cast<std::size_t>(kSixMonths + 2)) {
        throw std::runtime_error("benchmark has insufficient price history");
    }

    for (const auto& [ticker, observations] : raw) {
        std::vector<double> aligned(result.dates.size(), kNaN);
        double last = kNaN;
        for (std::size_t index = 0; index < result.dates.size(); ++index) {
            const auto found = observations.find(result.dates[index]);
            if (found != observations.end()) {
                last = found->second;
            }
            if (finite(last)) {
                aligned[index] = last;
            }
        }
        result.adjusted_close.emplace(ticker, std::move(aligned));
    }
    return result;
}

const std::vector<double>& PriceTable::series(const std::string& ticker) const {
    const auto found = adjusted_close.find(ticker);
    if (found == adjusted_close.end()) {
        throw std::runtime_error("price history is missing ticker " + ticker);
    }
    return found->second;
}

std::vector<Security> load_universe_csv(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open universe file: " + path);
    }
    std::string line;
    if (!std::getline(input, line)) {
        throw std::runtime_error("universe file is empty: " + path);
    }
    const auto columns = header_map(parse_csv_line(line));
    const auto ticker = require_column(columns, "ticker", path);
    const auto company = require_column(columns, "company", path);
    const auto sector = require_column(columns, "sector", path);
    const auto industry = require_column(columns, "industry", path);
    const auto sector_etf = require_column(columns, "sector_etf", path);

    std::vector<Security> result;
    std::set<std::string> seen;
    std::size_t row_number = 1;
    while (std::getline(input, line)) {
        ++row_number;
        if (trim(line).empty()) {
            continue;
        }
        const auto fields = parse_csv_line(line);
        const std::size_t required =
            std::max({ticker, company, sector, industry, sector_etf});
        if (fields.size() <= required) {
            throw std::runtime_error(path + ": short row " + std::to_string(row_number));
        }
        Security security{fields[ticker], fields[company], fields[sector], fields[industry],
                          fields[sector_etf]};
        if (security.ticker.empty() || security.sector.empty() || security.industry.empty() ||
            security.sector_etf.empty()) {
            throw std::runtime_error(path + ": blank required value on row " +
                                     std::to_string(row_number));
        }
        if (!seen.insert(security.ticker).second) {
            throw std::runtime_error(path + ": duplicate ticker " + security.ticker);
        }
        result.push_back(std::move(security));
    }
    if (result.empty()) {
        throw std::runtime_error("universe contains no securities");
    }
    return result;
}

Engine::Engine(PriceTable prices, std::vector<Security> universe, Config config)
    : prices_(std::move(prices)), universe_(std::move(universe)), config_(std::move(config)) {
    (void)prices_.series(config_.benchmark);
    std::map<std::string, std::vector<std::string>> groups;
    std::map<std::string, std::string> sector_etfs;
    for (const auto& security : universe_) {
        (void)prices_.series(security.ticker);
        (void)prices_.series(security.sector_etf);
        groups[industry_key(security.sector, security.industry)].push_back(security.ticker);
        const auto [position, inserted] =
            sector_etfs.emplace(security.sector, security.sector_etf);
        if (!inserted && position->second != security.sector_etf) {
            throw std::runtime_error("sector maps to multiple ETFs: " + security.sector);
        }
    }
    for (const auto& [key, tickers] : groups) {
        industry_indexes_[key] = build_equal_weight_index(tickers, prices_);
    }
}

std::vector<Selection> Engine::select_portfolio(std::size_t signal_index) const {
    const auto& benchmark = prices_.series(config_.benchmark);
    std::map<std::string, std::string> sector_etfs;
    for (const auto& security : universe_) {
        sector_etfs.emplace(security.sector, security.sector_etf);
    }

    std::vector<std::pair<std::string, MomentumMetrics>> sector_metrics;
    for (const auto& [sector, etf] : sector_etfs) {
        const auto metrics = momentum_metrics(prices_.series(etf), benchmark, signal_index);
        if (metrics) {
            sector_metrics.emplace_back(sector, *metrics);
        }
    }
    auto scored_sectors = score_assets(sector_metrics);
    if (scored_sectors.size() > static_cast<std::size_t>(config_.top_sectors)) {
        scored_sectors.resize(static_cast<std::size_t>(config_.top_sectors));
    }
    if (scored_sectors.empty()) {
        return {};
    }

    std::vector<Selection> selections;
    const double sector_budget = std::min(
        1.0 / static_cast<double>(scored_sectors.size()), config_.maximum_sector_weight);
    for (const auto& scored_sector : scored_sectors) {
        const std::string& sector = scored_sector.key;
        const std::string& sector_etf = sector_etfs.at(sector);
        std::set<std::string> industries;
        for (const auto& security : universe_) {
            if (security.sector == sector) {
                industries.insert(security.industry);
            }
        }

        std::vector<std::pair<std::string, MomentumMetrics>> industry_metrics;
        for (const auto& industry : industries) {
            const auto& index = industry_indexes_.at(industry_key(sector, industry));
            const auto metrics =
                momentum_metrics(index, prices_.series(sector_etf), signal_index);
            if (metrics) {
                industry_metrics.emplace_back(industry, *metrics);
            }
        }
        auto scored_industries = score_assets(industry_metrics);
        if (scored_industries.size() >
            static_cast<std::size_t>(config_.top_industries_per_sector)) {
            scored_industries.resize(
                static_cast<std::size_t>(config_.top_industries_per_sector));
        }
        if (scored_industries.empty()) {
            continue;
        }
        const double industry_budget =
            sector_budget / static_cast<double>(scored_industries.size());

        for (const auto& scored_industry : scored_industries) {
            std::vector<std::pair<std::string, MomentumMetrics>> company_metrics;
            std::map<std::string, const Security*> security_by_ticker;
            for (const auto& security : universe_) {
                if (security.sector != sector || security.industry != scored_industry.key) {
                    continue;
                }
                const auto& company_prices = prices_.series(security.ticker);
                const auto metrics = momentum_metrics(company_prices, benchmark, signal_index);
                if (metrics &&
                    above_moving_average(company_prices, signal_index, config_.trend_days)) {
                    company_metrics.emplace_back(security.ticker, *metrics);
                    security_by_ticker.emplace(security.ticker, &security);
                }
            }
            auto scored_companies = score_assets(company_metrics);
            if (scored_companies.size() >
                static_cast<std::size_t>(config_.top_companies_per_industry)) {
                scored_companies.resize(
                    static_cast<std::size_t>(config_.top_companies_per_industry));
            }
            if (scored_companies.empty()) {
                continue;
            }

            double weight_denominator = static_cast<double>(scored_companies.size());
            if (config_.inverse_volatility_weights) {
                weight_denominator = 0.0;
                for (const auto& company : scored_companies) {
                    weight_denominator += 1.0 / std::max(company.metrics.volatility_3m, 1e-9);
                }
            }
            for (const auto& company : scored_companies) {
                const double raw_share = config_.inverse_volatility_weights
                                             ? (1.0 / std::max(
                                                    company.metrics.volatility_3m, 1e-9)) /
                                                   weight_denominator
                                             : 1.0 / weight_denominator;
                const double target_weight = std::min(
                    industry_budget * raw_share, config_.maximum_position_weight);
                const Security& security = *security_by_ticker.at(company.key);
                selections.push_back(Selection{
                    prices_.dates[signal_index],
                    prices_.dates[signal_index],
                    security.ticker,
                    security.company,
                    security.sector,
                    security.industry,
                    scored_sector.score,
                    scored_industry.score,
                    company.score,
                    company.metrics.volatility_3m,
                    target_weight,
                });
            }
        }
    }
    return selections;
}

Result Engine::run() const {
    const std::size_t warmup = static_cast<std::size_t>(
        std::max(kSixMonths, std::max(0, config_.trend_days - 1)));
    std::size_t start_index = warmup;
    if (config_.start_date) {
        const auto found = std::lower_bound(
            prices_.dates.begin(), prices_.dates.end(), *config_.start_date);
        if (found == prices_.dates.end()) {
            throw std::runtime_error("start date is after available price history");
        }
        start_index = std::max(start_index,
                               static_cast<std::size_t>(found - prices_.dates.begin()));
    }
    std::size_t end_index = prices_.dates.size() - 1;
    if (config_.end_date) {
        const auto found = std::upper_bound(
            prices_.dates.begin(), prices_.dates.end(), *config_.end_date);
        if (found == prices_.dates.begin()) {
            throw std::runtime_error("end date is before available price history");
        }
        end_index = static_cast<std::size_t>((found - prices_.dates.begin()) - 1);
    }
    if (start_index + 2 > end_index) {
        throw std::runtime_error("backtest window is too short after lookback warm-up");
    }

    Result result;
    result.config = config_;
    result.start_date = prices_.dates[start_index];
    result.end_date = prices_.dates[end_index];
    double cash = config_.initial_capital;
    std::map<std::string, double> shares;
    std::optional<std::vector<Selection>> pending;
    std::size_t pending_execution_index = 0;
    int next_signal_month = month_number(prices_.dates[start_index]);
    bool first_signal = true;
    double running_peak = config_.initial_capital;
    double previous_value = config_.initial_capital;
    const auto& benchmark = prices_.series(config_.benchmark);
    const double benchmark_start = benchmark[start_index];
    if (!finite(benchmark_start) || benchmark_start <= 0.0) {
        throw std::runtime_error("benchmark is missing at performance start");
    }

    for (std::size_t date_index = start_index; date_index <= end_index; ++date_index) {
        if (pending && date_index == pending_execution_index) {
            const double equity_before =
                portfolio_value(cash, shares, prices_, date_index);
            std::map<std::string, double> target_weights;
            for (const auto& selection : *pending) {
                target_weights[selection.ticker] = selection.target_weight;
            }

            std::set<std::string> traded_tickers;
            for (const auto& [ticker, quantity] : shares) {
                (void)quantity;
                traded_tickers.insert(ticker);
            }
            for (const auto& [ticker, weight] : target_weights) {
                (void)weight;
                traded_tickers.insert(ticker);
            }

            const double cost_rate = config_.transaction_cost_bps / 10000.0;
            double investable = equity_before;
            double traded_notional = 0.0;
            for (int iteration = 0; iteration < 5; ++iteration) {
                traded_notional = 0.0;
                for (const auto& ticker : traded_tickers) {
                    const double price = prices_.series(ticker)[date_index];
                    const double current = shares[ticker] * price;
                    const double target = investable * target_weights[ticker];
                    traded_notional += std::abs(target - current);
                }
                investable = std::max(0.0, equity_before - traded_notional * cost_rate);
            }

            std::map<std::string, double> new_shares;
            double target_invested = 0.0;
            double total_cost = 0.0;
            for (const auto& ticker : traded_tickers) {
                const double price = prices_.series(ticker)[date_index];
                const double current_quantity = shares[ticker];
                const double target_value = investable * target_weights[ticker];
                const double target_quantity = target_value / price;
                const double quantity_change = target_quantity - current_quantity;
                const double notional = std::abs(quantity_change * price);
                if (notional > 1e-8) {
                    const double trade_cost = notional * cost_rate;
                    result.trades.push_back(Trade{
                        prices_.dates[date_index],
                        ticker,
                        quantity_change > 0.0 ? "BUY" : "SELL",
                        std::abs(quantity_change),
                        price,
                        notional,
                        trade_cost,
                    });
                    total_cost += trade_cost;
                }
                if (target_quantity > 1e-12) {
                    new_shares[ticker] = target_quantity;
                    target_invested += target_value;
                }
            }
            result.total_turnover +=
                equity_before > 0.0 ? traded_notional / equity_before : 0.0;
            result.total_transaction_costs += total_cost;
            cash = equity_before - target_invested - total_cost;
            shares = std::move(new_shares);
            ++result.rebalance_count;
            pending.reset();
        }

        const double value = portfolio_value(cash, shares, prices_, date_index);
        const double daily_return =
            result.equity_curve.empty() ? 0.0 : value / previous_value - 1.0;
        running_peak = std::max(running_peak, value);
        result.equity_curve.push_back(EquityPoint{
            prices_.dates[date_index],
            value,
            daily_return,
            config_.initial_capital * benchmark[date_index] / benchmark_start,
            value / running_peak - 1.0,
            value > 0.0 ? cash / value : 0.0,
        });
        previous_value = value;

        const int current_month = month_number(prices_.dates[date_index]);
        if (date_index < end_index && !pending &&
            (first_signal || current_month >= next_signal_month)) {
            auto selections = select_portfolio(date_index);
            for (auto& selection : selections) {
                selection.execution_date = prices_.dates[date_index + 1];
                result.selections.push_back(selection);
            }
            pending = std::move(selections);
            pending_execution_index = date_index + 1;
            first_signal = false;
            next_signal_month = current_month + config_.rebalance_months;
        }
    }

    std::vector<Date> dates;
    std::vector<double> portfolio_values;
    std::vector<double> benchmark_values;
    for (const auto& point : result.equity_curve) {
        dates.push_back(point.date);
        portfolio_values.push_back(point.portfolio_value);
        benchmark_values.push_back(point.benchmark_value);
    }
    result.portfolio = calculate_performance(dates, portfolio_values);
    result.benchmark = calculate_performance(dates, benchmark_values);
    return result;
}

void write_outputs(const Result& result, const std::string& output_directory) {
    namespace fs = std::filesystem;
    fs::create_directories(output_directory);

    std::ofstream equity(fs::path(output_directory) / "equity_curve.csv");
    equity << "date,portfolio_value,daily_return,benchmark_value,drawdown,cash_weight\n";
    equity << std::setprecision(12);
    for (const auto& point : result.equity_curve) {
        equity << point.date.str() << ',' << point.portfolio_value << ',' << point.daily_return
               << ',' << point.benchmark_value << ',' << point.drawdown << ','
               << point.cash_weight << '\n';
    }

    std::ofstream trades(fs::path(output_directory) / "trades.csv");
    trades << "date,ticker,action,shares,price,notional,transaction_cost\n";
    trades << std::setprecision(12);
    for (const auto& trade : result.trades) {
        trades << trade.date.str() << ',' << csv_escape(trade.ticker) << ',' << trade.action << ','
               << trade.shares << ',' << trade.price << ',' << trade.notional << ','
               << trade.transaction_cost << '\n';
    }

    std::ofstream selections(fs::path(output_directory) / "rebalance_log.csv");
    selections << "signal_date,execution_date,ticker,company,sector,industry,sector_score,"
                  "industry_score,company_score,volatility_3m,target_weight\n";
    selections << std::setprecision(12);
    for (const auto& selection : result.selections) {
        selections << selection.signal_date.str() << ',' << selection.execution_date.str() << ','
                   << csv_escape(selection.ticker) << ',' << csv_escape(selection.company) << ','
                   << csv_escape(selection.sector) << ',' << csv_escape(selection.industry) << ','
                   << selection.sector_score << ',' << selection.industry_score << ','
                   << selection.company_score << ',' << selection.volatility_3m << ','
                   << selection.target_weight << '\n';
    }

    std::ofstream summary(fs::path(output_directory) / "summary.csv");
    summary << "metric,portfolio,benchmark\n" << std::setprecision(12);
    summary << "ending_value," << result.portfolio.ending_value << ','
            << result.benchmark.ending_value << '\n';
    summary << "total_return," << result.portfolio.total_return << ','
            << result.benchmark.total_return << '\n';
    summary << "cagr," << result.portfolio.cagr << ',' << result.benchmark.cagr << '\n';
    summary << "annualized_volatility," << result.portfolio.annualized_volatility << ','
            << result.benchmark.annualized_volatility << '\n';
    summary << "sharpe_ratio," << result.portfolio.sharpe_ratio << ','
            << result.benchmark.sharpe_ratio << '\n';
    summary << "sortino_ratio," << result.portfolio.sortino_ratio << ','
            << result.benchmark.sortino_ratio << '\n';
    summary << "maximum_drawdown," << result.portfolio.maximum_drawdown << ','
            << result.benchmark.maximum_drawdown << '\n';
    summary << "total_turnover," << result.total_turnover << ",\n";
    summary << "transaction_costs," << result.total_transaction_costs << ",\n";
    summary << "rebalance_count," << result.rebalance_count << ",\n";

    std::ofstream run_config(fs::path(output_directory) / "run_config.csv");
    run_config << "parameter,value\n";
    run_config << "benchmark," << csv_escape(result.config.benchmark) << '\n';
    run_config << "start_date," << result.start_date.str() << '\n';
    run_config << "end_date," << result.end_date.str() << '\n';
    run_config << "initial_capital," << result.config.initial_capital << '\n';
    run_config << "top_sectors," << result.config.top_sectors << '\n';
    run_config << "top_industries_per_sector," << result.config.top_industries_per_sector
               << '\n';
    run_config << "top_companies_per_industry," << result.config.top_companies_per_industry
               << '\n';
    run_config << "rebalance_months," << result.config.rebalance_months << '\n';
    run_config << "trend_days," << result.config.trend_days << '\n';
    run_config << "transaction_cost_bps," << result.config.transaction_cost_bps << '\n';
    run_config << "maximum_position_weight," << result.config.maximum_position_weight << '\n';
    run_config << "maximum_sector_weight," << result.config.maximum_sector_weight << '\n';
    run_config << "weighting,"
               << (result.config.inverse_volatility_weights ? "inverse-vol" : "equal") << '\n';
}

}  // namespace backtest

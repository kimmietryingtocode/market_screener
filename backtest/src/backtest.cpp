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
    const std::vector<double>& values,
    double initial_value) {
    if (dates.size() != values.size() || dates.size() < 2 || initial_value <= 0.0) {
        throw std::runtime_error("insufficient observations for performance statistics");
    }
    Performance result;
    result.ending_value = values.back();
    result.total_return = values.back() / initial_value - 1.0;
    const double years =
        static_cast<double>(dates.back().serial() - dates.front().serial()) / 365.25;
    result.cagr = years > 0.0 && values.back() > 0.0
                      ? std::pow(values.back() / initial_value, 1.0 / years) - 1.0
                      : kNaN;

    std::vector<double> daily_returns;
    daily_returns.reserve(values.size());
    daily_returns.push_back(values.front() / initial_value - 1.0);
    double peak = initial_value;
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            daily_returns.push_back(values[index] / values[index - 1] - 1.0);
        }
        peak = std::max(peak, values[index]);
        const double drawdown = values[index] / peak - 1.0;
        result.maximum_drawdown = std::min(result.maximum_drawdown, drawdown);
    }
    const double daily_mean =
        std::accumulate(daily_returns.begin(), daily_returns.end(), 0.0) /
        daily_returns.size();
    const double daily_volatility = sample_standard_deviation(daily_returns);
    result.annualized_volatility = daily_volatility * std::sqrt(kTradingDaysPerYear);
    result.sharpe_ratio = daily_volatility > 0.0
                              ? daily_mean / daily_volatility * std::sqrt(kTradingDaysPerYear)
                              : kNaN;
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
    if (result.dates.size() < 2) {
        throw std::runtime_error("benchmark requires at least two price observations");
    }

    for (const auto& [ticker, observations] : raw) {
        std::vector<double> aligned(result.dates.size(), kNaN);
        for (std::size_t index = 0; index < result.dates.size(); ++index) {
            const auto found = observations.find(result.dates[index]);
            if (found != observations.end()) {
                aligned[index] = found->second;
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

std::vector<TargetWeight> load_target_schedule_csv(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open target schedule: " + path);
    }
    std::string line;
    if (!std::getline(input, line)) {
        throw std::runtime_error("target schedule is empty: " + path);
    }
    const auto columns = header_map(parse_csv_line(line));
    const auto signal_at = require_column(columns, "signal_at", path);
    const auto execution_date = require_column(columns, "execution_date", path);
    const auto security_id = require_column(columns, "security_id", path);
    const auto target_weight = require_column(columns, "target_weight", path);
    const auto provenance_id = require_column(columns, "provenance_id", path);

    std::vector<TargetWeight> result;
    std::size_t row_number = 1;
    while (std::getline(input, line)) {
        ++row_number;
        if (trim(line).empty()) {
            continue;
        }
        const auto fields = parse_csv_line(line);
        const std::size_t required =
            std::max({signal_at, execution_date, security_id, target_weight, provenance_id});
        if (fields.size() <= required) {
            throw std::runtime_error(path + ": short row " + std::to_string(row_number));
        }
        TargetWeight row{fields[signal_at],
                         Date::parse(fields[execution_date]),
                         fields[security_id],
                         std::stod(fields[target_weight]),
                         fields[provenance_id]};
        if (row.signal_at.size() < 10 || row.target_weight < 0.0 || row.target_weight > 1.0) {
            throw std::runtime_error(path + ": invalid target row " +
                                     std::to_string(row_number));
        }
        result.push_back(std::move(row));
    }
    std::sort(result.begin(), result.end(), [](const auto& lhs, const auto& rhs) {
        return std::tie(lhs.execution_date, lhs.signal_at, lhs.security_id) <
               std::tie(rhs.execution_date, rhs.signal_at, rhs.security_id);
    });
    return result;
}

Result run_target_schedule(
    const PriceTable& prices,
    const std::vector<TargetWeight>& targets,
    const Config& config) {
    if (targets.empty()) {
        throw std::runtime_error("target schedule contains no rebalances");
    }
    std::size_t start_index = 0;
    if (config.start_date) {
        const auto found = std::lower_bound(prices.dates.begin(), prices.dates.end(), *config.start_date);
        if (found == prices.dates.end()) {
            throw std::runtime_error("start date is after available price history");
        }
        start_index = static_cast<std::size_t>(found - prices.dates.begin());
    }
    std::size_t end_index = prices.dates.size() - 1;
    if (config.end_date) {
        const auto found = std::upper_bound(prices.dates.begin(), prices.dates.end(), *config.end_date);
        if (found == prices.dates.begin()) {
            throw std::runtime_error("end date is before available price history");
        }
        end_index = static_cast<std::size_t>((found - prices.dates.begin()) - 1);
    }
    if (start_index + 1 > end_index) {
        throw std::runtime_error("target-schedule window is too short");
    }

    std::map<Date, std::vector<TargetWeight>> targets_by_date;
    for (const auto& target : targets) {
        if (target.execution_date < prices.dates[start_index] ||
            prices.dates[end_index] < target.execution_date) {
            continue;
        }
        targets_by_date[target.execution_date].push_back(target);
        if (!target.security_id.empty()) {
            (void)prices.series(target.security_id);
        }
    }

    Result result;
    result.config = config;
    result.start_date = prices.dates[start_index];
    result.end_date = prices.dates[end_index];
    double cash = config.initial_capital;
    std::map<std::string, double> shares;
    double running_peak = config.initial_capital;
    double previous_value = config.initial_capital;
    bool has_invested = false;
    const auto& benchmark = prices.series(config.benchmark);
    const double benchmark_start = benchmark[start_index];
    if (!finite(benchmark_start) || benchmark_start <= 0.0) {
        throw std::runtime_error("benchmark is missing at performance start");
    }

    for (std::size_t date_index = start_index; date_index <= end_index; ++date_index) {
        const double pre_trade_value = portfolio_value(cash, shares, prices, date_index);
        double session_traded_notional = 0.0;
        double session_transaction_cost = 0.0;
        const auto found_targets = targets_by_date.find(prices.dates[date_index]);
        if (found_targets != targets_by_date.end()) {
            const double equity_before = pre_trade_value;
            std::map<std::string, double> target_weights;
            double total_weight = 0.0;
            for (const auto& target : found_targets->second) {
                result.executed_targets.push_back(target);
                if (target.security_id.empty()) {
                    continue;
                }
                if (!target_weights.emplace(target.security_id, target.target_weight).second) {
                    throw std::runtime_error("duplicate target for " + target.security_id +
                                             " on " + target.execution_date.str());
                }
                total_weight += target.target_weight;
            }
            if (total_weight > 1.0 + 1e-10) {
                throw std::runtime_error("target weights exceed 100% on " +
                                         prices.dates[date_index].str());
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

            const double cost_rate = config.transaction_cost_bps / 10000.0;
            double investable = equity_before;
            double traded_notional = 0.0;
            for (int iteration = 0; iteration < 100; ++iteration) {
                traded_notional = 0.0;
                for (const auto& ticker : traded_tickers) {
                    const double price = prices.series(ticker)[date_index];
                    if (!finite(price) || price <= 0.0) {
                        throw std::runtime_error("missing execution price for " + ticker);
                    }
                    const double current = shares[ticker] * price;
                    const double target = investable * target_weights[ticker];
                    traded_notional += std::abs(target - current);
                }
                const double next = std::max(0.0, equity_before - traded_notional * cost_rate);
                if (std::abs(next - investable) <=
                    std::max(1.0, equity_before) * 1e-14) {
                    investable = next;
                    break;
                }
                investable = next;
            }

            std::map<std::string, double> new_shares;
            double target_invested = 0.0;
            double total_cost = 0.0;
            double actual_traded_notional = 0.0;
            for (const auto& ticker : traded_tickers) {
                const double price = prices.series(ticker)[date_index];
                const double current_quantity = shares[ticker];
                const double target_value = investable * target_weights[ticker];
                const double target_quantity = target_value / price;
                const double quantity_change = target_quantity - current_quantity;
                const double notional = std::abs(quantity_change * price);
                if (notional > 1e-8) {
                    const double trade_cost = notional * cost_rate;
                    result.trades.push_back(Trade{
                        prices.dates[date_index],
                        ticker,
                        quantity_change > 0.0 ? "BUY" : "SELL",
                        std::abs(quantity_change),
                        price,
                        notional,
                        trade_cost,
                    });
                    total_cost += trade_cost;
                    actual_traded_notional += notional;
                }
                if (target_quantity > 1e-12) {
                    new_shares[ticker] = target_quantity;
                    target_invested += target_value;
                }
            }
            result.total_turnover +=
                equity_before > 0.0 ? actual_traded_notional / equity_before : 0.0;
            result.total_transaction_costs += total_cost;
            cash = equity_before - target_invested - total_cost;
            if (cash < -1e-6) {
                throw std::runtime_error("rebalance produced negative cash on " +
                                         prices.dates[date_index].str());
            }
            if (cash < 0.0) cash = 0.0;
            shares = std::move(new_shares);
            session_traded_notional = actual_traded_notional;
            session_transaction_cost = total_cost;
            ++result.rebalance_count;
        }

        const double value = portfolio_value(cash, shares, prices, date_index);
        const double daily_return = value / previous_value - 1.0;
        running_peak = std::max(running_peak, value);
        result.equity_curve.push_back(EquityPoint{
            prices.dates[date_index],
            value,
            daily_return,
            config.initial_capital * benchmark[date_index] / benchmark_start,
            value / running_peak - 1.0,
            value > 0.0 ? cash / value : 0.0,
        });
        double invested_value = 0.0;
        for (const auto& [ticker, units] : shares) {
            const double price = prices.series(ticker)[date_index];
            const double market_value = units * price;
            invested_value += market_value;
            result.holdings.push_back(HoldingPoint{
                prices.dates[date_index], ticker, units, price, market_value,
                value > 0.0 ? market_value / value : 0.0,
            });
        }
        result.account_ledger.push_back(AccountPoint{
            prices.dates[date_index], pre_trade_value, cash, invested_value,
            session_traded_notional, session_transaction_cost, value,
        });
        if (!has_invested && shares.empty()) {
            ++result.initial_waiting_sessions;
        } else if (!shares.empty()) {
            has_invested = true;
        }
        previous_value = value;
    }

    std::vector<Date> dates;
    std::vector<double> portfolio_values;
    std::vector<double> benchmark_values;
    for (const auto& point : result.equity_curve) {
        dates.push_back(point.date);
        portfolio_values.push_back(point.portfolio_value);
        benchmark_values.push_back(point.benchmark_value);
    }
    result.portfolio = calculate_performance(dates, portfolio_values, config.initial_capital);
    result.benchmark = calculate_performance(dates, benchmark_values, config.initial_capital);
    result.average_cash_weight = std::accumulate(
        result.equity_curve.begin(), result.equity_curve.end(), 0.0,
        [](double total, const EquityPoint& point) { return total + point.cash_weight; }) /
        static_cast<double>(result.equity_curve.size());
    return result;
}

void write_outputs(const Result& result, const std::string& output_directory) {
    namespace fs = std::filesystem;
    fs::create_directories(output_directory);

    std::ofstream equity(fs::path(output_directory) / "equity_curve.csv");
    equity << "date,portfolio_value,daily_return,benchmark_value,drawdown,cash_weight\n";
    equity << std::setprecision(17);
    for (const auto& point : result.equity_curve) {
        equity << point.date.str() << ',' << point.portfolio_value << ',' << point.daily_return
               << ',' << point.benchmark_value << ',' << point.drawdown << ','
               << point.cash_weight << '\n';
    }

    std::ofstream ledger(fs::path(output_directory) / "account_ledger.csv");
    ledger << "date,pre_trade_value,cash,invested_value,traded_notional,transaction_cost,portfolio_value\n";
    ledger << std::setprecision(17);
    for (const auto& point : result.account_ledger) {
        ledger << point.date.str() << ',' << point.pre_trade_value << ',' << point.cash << ','
               << point.invested_value << ',' << point.traded_notional << ','
               << point.transaction_cost << ',' << point.portfolio_value << '\n';
    }

    std::ofstream holdings(fs::path(output_directory) / "holdings.csv");
    holdings << "date,ticker,units,price,market_value,weight\n" << std::setprecision(17);
    for (const auto& point : result.holdings) {
        holdings << point.date.str() << ',' << csv_escape(point.ticker) << ',' << point.units
                 << ',' << point.price << ',' << point.market_value << ',' << point.weight << '\n';
    }

    std::ofstream trades(fs::path(output_directory) / "trades.csv");
    trades << "date,ticker,action,units,price,notional,transaction_cost\n";
    trades << std::setprecision(17);
    for (const auto& trade : result.trades) {
        trades << trade.date.str() << ',' << csv_escape(trade.ticker) << ',' << trade.action << ','
               << trade.units << ',' << trade.price << ',' << trade.notional << ','
               << trade.transaction_cost << '\n';
    }

    std::ofstream selections(fs::path(output_directory) / "rebalance_log.csv");
    selections << "signal_at,execution_date,security_id,target_weight,provenance_id\n";
    selections << std::setprecision(17);
    for (const auto& target : result.executed_targets) {
        selections << csv_escape(target.signal_at) << ',' << target.execution_date.str() << ','
                   << csv_escape(target.security_id) << ',' << target.target_weight << ','
                   << csv_escape(target.provenance_id) << '\n';
    }

    std::ofstream summary(fs::path(output_directory) / "summary.csv");
    summary << "metric,portfolio,benchmark\n" << std::setprecision(17);
    summary << "ending_value," << result.portfolio.ending_value << ','
            << result.benchmark.ending_value << '\n';
    summary << "total_return," << result.portfolio.total_return << ','
            << result.benchmark.total_return << '\n';
    summary << "cagr," << result.portfolio.cagr << ',' << result.benchmark.cagr << '\n';
    summary << "annualized_volatility," << result.portfolio.annualized_volatility << ','
            << result.benchmark.annualized_volatility << '\n';
    summary << "sharpe_ratio," << result.portfolio.sharpe_ratio << ','
            << result.benchmark.sharpe_ratio << '\n';
    summary << "maximum_drawdown," << result.portfolio.maximum_drawdown << ','
            << result.benchmark.maximum_drawdown << '\n';
    summary << "total_turnover," << result.total_turnover << ",\n";
    summary << "transaction_costs," << result.total_transaction_costs << ",\n";
    summary << "rebalance_count," << result.rebalance_count << ",\n";
    summary << "average_cash_weight," << result.average_cash_weight << ",\n";
    summary << "initial_waiting_sessions," << result.initial_waiting_sessions << ",\n";

    std::ofstream notes(fs::path(output_directory) / "statistics_notes.csv");
    notes << "metric,definition,undefined_when\n"
          << "total_return,ending value divided by starting capital minus one,never for a valid run\n"
          << "cagr,actual elapsed calendar time,nonpositive ending value or zero elapsed time\n"
          << "annualized_volatility,sample daily standard deviation times sqrt(252),fewer than two returns\n"
          << "sharpe_ratio,mean daily return divided by sample standard deviation times sqrt(252),zero volatility\n"
          << "maximum_drawdown,includes starting capital,never for a valid run\n"
          << "total_turnover,sum of absolute traded notional divided by pre-trade equity,never for a valid run\n";

    std::ofstream run_config(fs::path(output_directory) / "run_config.csv");
    run_config << "parameter,value\n";
    run_config << "benchmark," << csv_escape(result.config.benchmark) << '\n';
    run_config << "start_date," << result.start_date.str() << '\n';
    run_config << "end_date," << result.end_date.str() << '\n';
    run_config << "initial_capital," << result.config.initial_capital << '\n';
    run_config << "transaction_cost_bps," << result.config.transaction_cost_bps << '\n';
}

}  // namespace backtest

# -*- coding: utf-8 -*-
import abc
import functools
import os
import sys
import time
from typing import Type

from pywinauto import findwindows

from . import perf_clock

import easyutils
import re

from . import grid_strategies, helpers, pop_dialog_handler
from .config import client
from win32gui import SetForegroundWindow, ShowWindow
import logging

if not sys.platform.startswith("darwin"):
    import pywinauto
    import pywinauto.clipboard


class IClientTrader(abc.ABC):
    @property
    @abc.abstractmethod
    def app(self):
        """Return current app instance"""
        pass

    @property
    @abc.abstractmethod
    def main(self):
        """Return current main window instance"""
        pass

    @property
    @abc.abstractmethod
    def config(self):
        """Return current config instance"""
        pass

    @abc.abstractmethod
    def wait(self, seconds: float):
        """Wait for operation return"""
        pass

    @abc.abstractmethod
    def refresh(self):
        """Refresh data"""
        pass


class ClientTrader(IClientTrader):
    _editor_need_type_keys = True
    # The strategy to use for getting grid data
    grid_strategy: Type[grid_strategies.IGridStrategy] = grid_strategies.Copy

    def __init__(self):
        self._config = client.create(self.broker_type)
        self._app = None
        self._main = None

    def _set_foreground(self, grid=None):
        if grid is None:
            grid = self._trader.main
        if grid.has_style(pywinauto.win32defines.WS_MINIMIZE):  # if minimized
            ShowWindow(grid.wrapper_object(), 9)  # restore window state
        else:
            SetForegroundWindow(grid.wrapper_object())  # bring to front

    @property
    def app(self):
        return self._app

    @property
    def main(self):
        return self._main

    @property
    def config(self):
        return self._config

    def connect(self, exe_path=None, **kwargs):
        """
        直接连接登陆后的客户端
        :param exe_path: 客户端路径类似 r'C:\\htzqzyb2\\xiadan.exe', 默认 r'C:\\htzqzyb2\\xiadan.exe'
        :return:
        """
        connect_path = exe_path or self._config.DEFAULT_EXE_PATH
        if connect_path is None:
            raise ValueError(
                "参数 exe_path 未设置，请设置客户端对应的 exe 地址,类似 C:\\客户端安装目录\\xiadan.exe"
            )

        self._app = pywinauto.Application().connect(
            path=connect_path, timeout=10
        )
        self._close_prompt_windows()
        self._main = self._app.top_window()

    @property
    def broker_type(self):
        return "ths"

    @property
    def balance(self):
        self._switch_left_menus(["查询[F4]", "资金股票"])

        return self._get_balance_from_statics()

    def _get_balance_from_statics(self):
        result = {}
        for key, control_id in self._config.BALANCE_CONTROL_ID_GROUP.items():
            result[key] = float(
                self._main.window(
                    control_id=control_id, class_name="Static"
                ).window_text()
            )
        return result

    @property
    def position(self):
        self._switch_left_menus(["查询[F4]", "资金股票"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def today_entrusts(self):
        self._switch_left_menus(["查询[F4]", "当日委托"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def today_trades(self):
        self._switch_left_menus(["查询[F4]", "当日成交"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def cancel_entrusts(self):
        self.refresh()
        self._switch_left_menus(["撤单[F3]"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @perf_clock()
    def cancel_entrust(self, entrust_no):
        self.refresh()
        for i, entrust in enumerate(self.cancel_entrusts):
            if (
                entrust[self._config.CANCEL_ENTRUST_ENTRUST_FIELD]
                == entrust_no
            ):
                self._cancel_entrust_by_double_click(i)
                return self._handle_pop_dialogs()
        return {"message": "委托单状态错误不能撤单, 该委托单可能已经成交或者已撤"}

    @perf_clock()
    def buy(self, security, price, amount, **kwargs):
        self._switch_left_menus(["买入[F1]"])

        return self.trade(security, price, amount)

    @perf_clock()
    def sell(self, security, price, amount, **kwargs):
        self._switch_left_menus(["卖出[F2]"])

        return self.trade(security, price, amount)

    @perf_clock()
    def market_buy(self, security, amount, ttype=None, limit_price=None, **kwargs):
        """
        市价买入
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']
        :param limit_price: 科创板 限价

        :return: {'entrust_no': '委托单号'}
        """
        self._switch_left_menus(["市价委托", "买入"])

        return self.market_trade(security, amount, ttype, limit_price=limit_price)

    @perf_clock()
    def market_sell(self, security, amount, ttype=None, limit_price=None, **kwargs):
        """
        市价卖出
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']
        :param limit_price: 科创板 限价
        :return: {'entrust_no': '委托单号'}
        """
        self._switch_left_menus(["市价委托", "卖出"])

        return self.market_trade(security, amount, ttype, limit_price=limit_price)

    def market_trade(self, security, amount, ttype=None, limit_price=None, **kwargs):
        """
        市价交易
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']

        :return: {'entrust_no': '委托单号'}
        """
        self._set_market_trade_params(security, amount, limit_price=limit_price)
        if ttype is not None:
            retry = 0
            retry_max = 10
            while retry < retry_max:
                try:
                    self._set_market_trade_type(ttype)
                    break
                except:
                    retry += 1
                    self.wait(0.1)

        self._submit_trade()

        return self._handle_pop_dialogs(
            handler_class=pop_dialog_handler.TradePopDialogHandler
        )

    def _set_market_trade_type(self, ttype):
        """根据选择的市价交易类型选择对应的下拉选项"""
        selects = self._app.top_window().window(
            control_id=self._config.TRADE_MARKET_TYPE_CONTROL_ID,
            class_name="ComboBox",
        )
        for i, text in enumerate(selects.texts()):
            # skip 0 index, because 0 index is current select index
            if i == 0:
                if re.search(ttype, text):  # 当前已经选中
                    return
                else:
                    continue
            if re.search(ttype, text):
                selects.select(i - 1)
                return
        raise TypeError("不支持对应的市价类型: {}".format(ttype))

    def auto_ipo(self):
        self._switch_left_menus(self._config.AUTO_IPO_MENU_PATH)

        stock_list = self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

        if len(stock_list) == 0:
            return {"message": "今日无新股"}
        invalid_list_idx = [
            # i for i, v in enumerate(stock_list) if v["申购数量"] <= 0 or v["证券代码"][:2] == "78"
            i for i, v in enumerate(stock_list) if v["申购数量"] <= 0
        ]

        if len(stock_list) == len(invalid_list_idx):
            return {"message": "没有发现可以申购的新股"}

        self._click(self._config.AUTO_IPO_SELECT_ALL_BUTTON_CONTROL_ID)
        self.wait(0.1)

        for row in invalid_list_idx:
            self._click_grid_by_row(row)
        self.wait(0.1)

        self._click(self._config.AUTO_IPO_BUTTON_CONTROL_ID)
        self.wait(0.1)

        return self._handle_pop_dialogs()

    def _click_grid_by_row(self, row):
        x = self._config.COMMON_GRID_LEFT_MARGIN
        y = (
            self._config.COMMON_GRID_FIRST_ROW_HEIGHT
            + self._config.COMMON_GRID_ROW_HEIGHT * row
        )
        self._app.top_window().window(
            control_id=self._config.COMMON_GRID_CONTROL_ID,
            class_name="CVirtualGridCtrl",
        ).click(coords=(x, y))

    @perf_clock()
    def _is_exist_pop_dialog(self):
        self.wait(0.5)  # wait dialog display
        try:
            return (
                self._main.wrapper_object()
                != self._app.top_window().wrapper_object()
            )
        except findwindows.ElementNotFoundError|pywinauto.timings.TimeoutError|RuntimeError as ex:
            logging.exception(ex)
            return False

    def _run_exe_path(self, exe_path):
        return os.path.join(os.path.dirname(exe_path), "xiadan.exe")

    def wait(self, seconds):
        time.sleep(seconds)

    def exit(self):
        self._app.kill()

    def _close_prompt_windows(self):
        self.wait(1)
        for window in self._app.windows(class_name="#32770"):
            if window.window_text() != self._config.TITLE:
                logging.info("close " + window.window_text())
                window.close()

                self.wait(0.2)
        self.wait(1)

    def close_pormpt_window_no_wait(self):
        for window in self._app.windows(class_name="#32770"):
            if window.window_text() != self._config.TITLE:
                window.close()


    def trade(self, security, price, amount):
        self._set_trade_params(security, price, amount)

        self._submit_trade()

        return self._handle_pop_dialogs(
            handler_class=pop_dialog_handler.TradePopDialogHandler
        )

    def _click(self, control_id):
        self._app.top_window().window(
            control_id=control_id, class_name="Button"
        ).click()

    @perf_clock()
    def _submit_trade(self):
        time.sleep(0.2)
        self._main.window(
            control_id=self._config.TRADE_SUBMIT_CONTROL_ID,
            class_name="Button",
        ).click()

    @perf_clock()
    def __get_top_window_pop_dialog(self):
        return self._app.top_window().window(control_id=self._config.POP_DIALOD_TITLE_CONTROL_ID)

    @perf_clock()
    def _get_pop_dialog_title(self):
        return (
            self.__get_top_window_pop_dialog()
            .window_text()
        )
    # def _get_pop_dialog_title(self):
    #     return (
    #         self._app.top_window()
    #         .window(control_id=self._config.POP_DIALOD_TITLE_CONTROL_ID)
    #         .window_text()
    #     )

    def _set_trade_params(self, security, price, amount):
        code = security[-6:]

        self._type_edit_control_keys(self._config.TRADE_SECURITY_CONTROL_ID, code)

        # wait security input finish
        self.wait(0.1)

        self._type_edit_control_keys(
            self._config.TRADE_PRICE_CONTROL_ID,
            easyutils.round_price_by_code(price, code),
        )
        self._type_edit_control_keys(self._config.TRADE_AMOUNT_CONTROL_ID, str(int(amount)))

    def _set_market_trade_params(self, security, amount, limit_price=None):
        code = security[-6:]

        self._type_edit_control_keys(self._config.TRADE_SECURITY_CONTROL_ID, code)

        # wait security input finish
        self.wait(0.1)

        self._type_edit_control_keys(self._config.TRADE_AMOUNT_CONTROL_ID, str(int(amount)))
        self.wait(0.1)
        price_control = None
        if str(security).startswith("68"):  # 科创板存在限价
            try:
                price_control = self._main.window(
                    control_id=self._config.TRADE_PRICE_CONTROL_ID, class_name="Edit"
                )
            except:
                pass
        if price_control is not None:
            price_control.set_edit_text(limit_price)


    def _get_grid_data(self, control_id):
        return self.grid_strategy(self).get(control_id)

    def _type_common_control_keys(self, control, text):
        self._set_foreground(control)
        control.type_keys(text, set_foreground=False)

    def _type_edit_control_keys(self, control_id, text):
        if not self._editor_need_type_keys:
            self._main.window(
                control_id=control_id, class_name="Edit"
            ).set_edit_text(text)
        else:
            editor = self._main.window(
                control_id=control_id, class_name="Edit")
            editor.select()
            editor.type_keys(text)

    def _collapse_left_menus(self):
        items = self._get_left_menus_handle().roots()
        for item in items:
            item.collapse()

    @perf_clock()
    def _switch_left_menus(self, path, sleep=0.2):
        self._get_left_menus_handle().get_item(path).click()
        self.wait(sleep)

    def _switch_left_menus_by_shortcut(self, shortcut, sleep=0.5):
        self._app.top_window().type_keys(shortcut)
        self.wait(sleep)

    @functools.lru_cache()
    def _get_left_menus_handle(self):
        count = 2
        while True:
            try:
                handle = self._main.window(
                    control_id=129, class_name="SysTreeView32"
                )
                if count <= 0:
                    return handle
                # sometime can't find handle ready, must retry
                handle.wait("ready", 2)
                return handle
            # pylint: disable=broad-except
            except Exception as ex:
                print(ex)
                pass
            count = count - 1

    def _cancel_entrust_by_double_click(self, row):
        x = self._config.CANCEL_ENTRUST_GRID_LEFT_MARGIN
        y = (
            self._config.CANCEL_ENTRUST_GRID_FIRST_ROW_HEIGHT
            + self._config.CANCEL_ENTRUST_GRID_ROW_HEIGHT * row
        )
        self._app.top_window().window(
            control_id=self._config.COMMON_GRID_CONTROL_ID,
            class_name="CVirtualGridCtrl",
        ).double_click(coords=(x, y))

    def refresh(self):
        self._switch_left_menus(["买入[F1]"], sleep=0.05)

    @perf_clock()
    def _handle_pop_dialogs(
        self, handler_class=pop_dialog_handler.PopDialogHandler
    ):
        handler = handler_class(self._app)

        while self._is_exist_pop_dialog():
            try:
                title = self._get_pop_dialog_title()
            except pywinauto.findwindows.ElementNotFoundError:
                return {"message": "success"}

            result = handler.handle(title)
            if result:
                return result
        return {"message": "success"}


class BaseLoginClientTrader(ClientTrader):
    @abc.abstractmethod
    def login(self, user, password, exe_path, comm_password=None, **kwargs):
        """Login Client Trader"""
        pass

    def prepare(
        self,
        config_path=None,
        user=None,
        password=None,
        exe_path=None,
        comm_password=None,
        **kwargs
    ):
        """
        登陆客户端
        :param config_path: 登陆配置文件，跟参数登陆方式二选一
        :param user: 账号
        :param password: 明文密码
        :param exe_path: 客户端路径类似 r'C:\\htzqzyb2\\xiadan.exe', 默认 r'C:\\htzqzyb2\\xiadan.exe'
        :param comm_password: 通讯密码
        :return:
        """
        if config_path is not None:
            account = helpers.file2dict(config_path)
            user = account["user"]
            password = account["password"]
            comm_password = account.get("comm_password")
            exe_path = account.get("exe_path")
        self.login(
            user,
            password,
            exe_path or self._config.DEFAULT_EXE_PATH,
            comm_password,
            **kwargs
        )

$( function() {
    $.tablesorter.addParser({
        id: "numberWithComma",
        is: function(s) {
            return /^[0-9]?[0-9,\.]*$/.test(s);
        },
        format: function(s) {
            return $.tablesorter.formatFloat(s.replace(/,/g, ''));
        },
        type: "numeric"
    });
});


function doStuff(data) {

    $("#polltable").remove()
    $("#info").append("<table id=\"polltable\" class=\"tablesorter\"><thead><tr>" +
          "<th class=\"tablesorter-header\" style =\"width:80px;\">Pollster</th>" +
          "<th class=\"tablesorter-header\">Date</th>" +
          "<th class=\"tablesorter-header\" style =\"width:150px;\">Region/Seat</th>" +
          "<th class=\"tablesorter-header\" style =\"width:65px;\">Polled</th>" +
          "<th class=\"tablesorter-header\" style =\"width:95px;\">Conservative</th>" +
          "<th class=\"tablesorter-header\" style =\"width:65px;\">Labour</th>" +
          "<th class=\"tablesorter-header\" style =\"width:75px;\">Lib Dems</th>" +
          "<th class=\"tablesorter-header\" style =\"width:55px;\">UKIP</th>" +
          "<th class=\"tablesorter-header\" style =\"width:60px;\">Green</th>" +
          "<th class=\"tablesorter-header\" style =\"width:50px;\">SNP</th>" +
          "<th class=\"tablesorter-header\" style =\"width:90px;\">Plaid Cymru</th>" +
          "<th class=\"tablesorter-header\" style =\"width:60px;\">Other</th>" +
          "<th class=\"tablesorter-header\" style =\"width:60px;\">SDLP</th>" +
          "<th class=\"tablesorter-header\" style =\"width:70px;\">Sinn Féin</th>" +
          "<th class=\"tablesorter-header\" style =\"width:70px;\">Alliance</th>" +
          "<th class=\"tablesorter-header\" style =\"width:50px;\">DUP</th>" +
          " <th class=\"tablesorter-header\" style =\"width:40px;\">UU</th>" +
          "</tr>" +
          "</thead>" +
          " <tbody id=\"polltablebody\"></tbody>" +
          "</table>")

		$.each(data, function(i){

      if (data[i].type == "seat")
        total = 100

      else
        total = data[i].total

			if (i == data.length -1)
					null

			else
        if (data[i].pollster != "me" && data[i].pollster != "whoever")
  				$("#polltablebody").append("<tr class=\"" + data[i].pollster +"\" + style=\"opacity: 0.85;\"><td style=\"text-align: left;\">" + pollsters[data[i].pollster] + "</td><td>" +
          data[i].date + "</td><td style=\"text-align: left;\">" + data[i].region + "</td><td>" + data[i].total +
          "</td><td>" + (100 * data[i].conservative/total).toFixed(0) +
          "</td><td>" + (100 * data[i].labour/total).toFixed(0) +
          "</td><td>" + (100 * data[i].libdems/total).toFixed(0) +
          "</td><td>" + (100 * data[i].ukip/total).toFixed(0) +
          "</td><td>" + (100 * data[i].green/total).toFixed(0) +
          "</td><td>" + (100 * data[i].snp/total).toFixed(0) +
          "</td><td>" + (100 * data[i].plaidcymru/total).toFixed(0)+
          "</td><td>" + (100 * data[i].other/total).toFixed(0) +
          "</td><td>" + (100 * data[i].sdlp/total).toFixed(0) +
          "</td><td>" + (100 * data[i].sinnfein/total).toFixed(0) +
          "</td><td>" + (100 * data[i].alliance/total).toFixed(0) +
          "</td><td>" + (100 * data[i].dup/total).toFixed(0) +
          "</td><td>" + (100 * data[i].uu/total).toFixed(0) +
          "</td></tr>")
		})


		$("#polltable").tablesorter({
      dateFormat: "ddmmyyyy",

      sortInitialOrder: "asc",
      headers: {
        1: { sorter: "shortDate"},
        3: { sortInitialOrder: 'desc' },
        4: { sortInitialOrder: 'desc' },
        5: { sortInitialOrder: 'desc' },
        6: { sortInitialOrder: 'desc' },
        7: { sortInitialOrder: 'desc' },
        8: { sortInitialOrder: 'desc' },
        9: { sortInitialOrder: 'desc' },
        10: { sortInitialOrder: 'desc' },
        11: { sortInitialOrder: 'desc' },
        12: { sortInitialOrder: 'desc' },
        13: { sortInitialOrder: 'desc' },
        14: { sortInitialOrder: 'desc' },
        15: { sortInitialOrder: 'desc' },
        16: { sortInitialOrder: 'desc' }

    },
			sortList:[[1,1]]

		});
};



function parseData(url, callBack) {
	Papa.parse(url, {
		download: true,
		header: true,
		dynamicTyping: true,
		complete: function(results) {
			callBack(results.data);
		}
	});
}
parseData("/election2015/data/polls.csv", doStuff);

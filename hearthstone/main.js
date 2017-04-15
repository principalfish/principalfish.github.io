var dict = {
  class : {
    "NEUTRAL" : "neutral",
    "WARRIOR" : "warrior",
    "ROGUE" : "rogue",
    "MAGE" : "mage",
    "PALADIN" : "paladin",
    "PRIEST" : "priest",
    "DRUID" : "druid",
    "WARLOCK" : "warlock",
    "SHAMAN" : "shaman",
    "HUNTER" : "hunter"
  },

  classText: {
    "NEUTRAL" : "Neutral",
    "WARRIOR" : "Warrior",
    "ROGUE" : "Rogue",
    "MAGE" : "Mage",
    "PALADIN" : "Paladin",
    "PRIEST" : "Priest",
    "DRUID" : "Druid",
    "WARLOCK" : "Warlock",
    "SHAMAN" : "Shaman",
    "HUNTER" : "Hunter"
  },

  type : {
    "MINION" : "Minion",
    "SPELL" : "Spell",
    "WEAPON" : "Weapon"
  },

  rarity : {
    "LEGENDARY" : "Legendary",
    "EPIC" : "Epic",
    "RARE" : "Rare",
    "COMMON" : "Common",
    "FREE" : "Free"
  },

  set : {
    "BRM": "Blackrock",
    "EXPERT1" : "Expert",
    "OG" : "Old Gods",
    "TGT" : "Grand Tourney",
    "GANGS" : "Gadgetzan",
    "KARA" : "Karazhan",
    "CORE" : "Core",
    "LOE" : "Explorers",
    "GVG" : "Goblins/Gnomes",
    "NAXX" : "Naxxramas",
    "REWARD" : "Reward",
    "UNGORO" : "Ungoro",
    "HOF" : "Hall of Fame"

  },

  mode : {
    "standard" : ["EXPERT1", "OG", "GANGS", "KARA", "CORE", "UNGORO"],
    "wild" : ["BRM", "TGT", "LOE", "GVG", "NAXX"]
  }

}
;var filters = {

    filterStates : {},

    filterStatesDefault : {

        "mode" : {
            "standard" : true,
            "wild" : false
        },

        "cardClass" : {
            "NEUTRAL" : true,
            "WARRIOR" : true,
            "ROGUE" : true,
            "MAGE" : true,
            "PALADIN" : true,
            "PRIEST" : true,
            "DRUID" : true,
            "WARLOCK" : true,
            "SHAMAN" : true,
            "HUNTER" : true
        },

        "type" : {
            "MINION" : true,
            "WEAPON" : true,
            "SPELL" : true
        },

        "rarity" : {
            "FREE" : true,
            "COMMON" : true,
            "RARE" : true,
            "EPIC" : true,
            "LEGENDARY" : true
        },

        "race" : {
            "" : true,
            "BEAST" : true,
            "ELEMENTAL" : true,
            "MECHANICAL" : true,
            "PIRATE" : true,
            "DEMON" : true,
            "DRAGON" : true,
            "MURLOC" : true,
            "TOTEM" : true
        },

        "cost" : {
            "0" : true,
            "1" : true,
            "2" : true,
            "3" : true,
            "4" : true,
            "5" : true,
            "6" : true,
            "7plus" : true
        }

    },

    mechanicFilter : "null",

    filteredList : [],

    resetFilter : function(){
        filters.filterStates = jQuery.extend(true, {}, filters.filterStatesDefault);
    },

    handle : function(div){
        var params = div.split("-");

        var upperCase = ["cardClass", "type", "rarity", "mechanics", "race"];

        if (upperCase.indexOf(params[1]) != -1){
            params[2] = params[2].toUpperCase();
        }

        filters.filterStates[params[1]][params[2]] = !filters.filterStates[params[1]][params[2]];

        if (filters.filterStates[params[1]][params[2]] == true){
            $("#" + div).removeClass("not-selected");
            $("#" + div).addClass("selected");
        } else {
            $("#" + div).removeClass("selected");
            $("#" + div).addClass("not-selected");
        }

        filters.filter(params[1]);
    },

    selectHandle : function(value){
        filters.mechanicFilter = value
        filters.filter();
    },

    filter : function(){
        filters.filteredList = [];
        sort.resetSort();

        $.each(cardData, function(card, data){
            data.filtered = true;
            // mode filter
            if (filters.filterStates.mode[data.mode] != true){
                data.filtered = false ;
            }
            //class filters
            else if (filters.filterStates.cardClass[data.cardClass] != true ){
                data.filtered = false;
            }
            // type filter
            else if (filters.filterStates.type[data.type] != true ){
                data.filtered = false;
            }
            // rarity filter
            else if (filters.filterStates.rarity[data.rarity] != true){
                data.filtered = false;
            }
            // tribe filter
            else if (filters.filterStates.race[data.race] != true){
                data.filtered = false;
            }
            // cost filter
            else if (filters.filterStates.cost[data.costStr] != true) {
                data.filtered = false;
            }
            // mechanic filter
            if (filters.mechanicFilter != "null"){
                if (data.mechanics.indexOf(filters.mechanicFilter) == -1){
                    data.filtered = false;
                }
            }


            if (data.filtered == true){
                filters.filteredList.push(data);
            }
        })

        sort.sortData("name", "asc");
        sort.changeHeader("display-name");
        filters.writeTable();
    },

    writeTable : function(){


      $("#display-cardstotal").text(filters.filteredList.length)

      $("#display-table-data").empty();

      $.each(filters.filteredList, function(i, data){

              var divider = "</div><div>";

              var toAdd = "<div onclick='filters.displayCard(\"" + data.url
               + "\")' class='card-row " + dict.class[data.cardClass] + "'><div>";


              toAdd += data.name + divider + dict.classText[data.cardClass] + divider + dict.type[data.type]
                        + divider + dict.rarity[data.rarity] + divider + dict.set[data.set] +
                        divider + data.cost + "</div></div>";
              $("#display-table-data").append(toAdd);

      });




    },


    displayCard : function(path){
        var url = "https://cdn.rawgit.com/schmich/hearthstone-card-images/" + path
        var toAdd =  "<img src='" + url + "' />"
        $("#card").html(toAdd)

    },

    selectAllStatus : false,

    selectAll: function(status){
        $.each(filters.filterStates, function(filter, states){
            $.each(states, function(key, val){

                states[key] = filters.selectAllStatus;

                if (filters.selectAllStatus == false){
                    $("#filters-" + filter + "-" + key.toLowerCase()).removeClass("selected");
                    $("#filters-" + filter + "-" + key.toLowerCase()).addClass("not-selected");
                    $('#filter-mechanic-select').prop('selectedIndex',0);
                    filters.mechanicFilter = "null";
                } else {
                    $("#filters-" + filter + "-" + key.toLowerCase()).removeClass("not-selected");
                    $("#filters-" + filter + "-" + key.toLowerCase()).addClass("selected");
                    $('#filter-mechanic-select').prop('selectedIndex',0);
                    filters.mechanicFilter = "null";
                }
            })
        })

        if (filters.selectAllStatus == true){
            $("#filters-select-button").text("Deselect All");
        } else {
            $("#filters-select-button").text("Select All");
        }

        filters.selectAllStatus = !filters.selectAllStatus;
        filters.filter();
    }

}
;function getData(){

	$.when(
		$.getJSON("cards_simple.json", function(data){

			cardData = data;
		})
	).then(function(){
		pageLoad();
	})
};

function pageLoad(){
	$.each(cardData, function(card, data){
		data["filtered"] = true;

		if (dict.mode.standard.indexOf(data.set) != -1){
			data.mode = "standard";
		} else {
			data.mode = "wild"
		}
	})

	// run filter and write table
	filters.resetFilter();
	filters.filter();
	filters.displayCard("f5d6/rel/OG_311.png")

}

var cardData = [];

function initialization(){

	$(document).ready(function(){
		getData();
	});
}

initialization();
;var sort = {

  sortStates : {},

  sortStatesDefault : {
    "name" : "asc",
    "cardClass" : "desc",
    "type" : "desc" ,
    "rarity" : "desc",
    "set" : "desc",
    "cost" : "asc"
  },

  resetSort : function(){
    sort.sortStates = jQuery.extend(true, {}, sort.sortStatesDefault);
  },

  sortTable : function(parameter){
    sort.changeHeader(parameter);
    parameter = parameter.slice(8)

    if (sort.sortStates[parameter] == "asc") {
      sort.sortStates[parameter] = "desc";
      sort.sortData(parameter, "desc");
    } else if (sort.sortStates[parameter] == "desc") {
      sort.sortStates[parameter] = "asc";
      sort.sortData(parameter, "asc");
    }

    filters.writeTable();

  },

  changeHeader : function(parameter){
    $(".sort-active").removeClass();
    $("#" + parameter).addClass("sort-active")
  },

  sortData : function(parameter, sorttype){

    var sortArray = filters.filteredList.map(function(data, ind){
      return {ind:ind, data:data}
    });

    // sort works both alphabetically and numerically
    if (sorttype == "asc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return -1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return 1;
        }
        return a.ind - b.ind;
      })
    }
    if (sorttype == "desc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return 1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return -1;
        }
        return a.ind - b.ind;
      })
    }


    filters.filteredList = sortArray.map(function(val){
      return val.data;
    });

  }
}
